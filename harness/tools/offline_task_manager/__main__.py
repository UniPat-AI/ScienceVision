"""Command line interface for the offline task management workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .bundle import (
    LEGACY_INTEGRITY_ALLOW_MISSING,
    LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
)
from .contract import ADMISSION_RECEIPT_PURPOSE, ContractError
from .packager import build_expert_package, build_shared_runtime
from .results_export import render_manager_results
from .store import ManagerStore, PreflightError, utc_now

DEFAULT_DB = Path(".local/offline-task-manager/tasks.db")


def _write_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _publish_new_file(output: Path, writer: Callable[[Path], None]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.staging-", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        writer(temporary)
        try:
            os.link(temporary, output)
        except FileExistsError as error:
            raise FileExistsError(f"refusing to overwrite existing export: {output}") from error
    finally:
        temporary.unlink(missing_ok=True)


def export_results(store: ManagerStore, output: Path, format_name: str) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing export: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = store.result_rows()
    payload, _content_type, _filename = render_manager_results(
        rows, format_name=format_name, exported_at=utc_now()
    )
    _publish_new_file(output, lambda path: path.write_bytes(payload))
    return {"output": str(output), "format": format_name, "result_count": len(rows)}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

    experts = commands.add_parser("import-experts")
    experts.add_argument("--csv", type=Path, required=True)

    batch = commands.add_parser("import-batch")
    batch.add_argument("--batch-id", required=True)
    batch.add_argument("--tasks-jsonl", type=Path, required=True)
    batch.add_argument("--asset-root", type=Path, required=True)
    batch.add_argument("--admission-receipt", type=Path, required=True)
    batch.add_argument(
        "--admission-purpose",
        choices=(ADMISSION_RECEIPT_PURPOSE,),
        default=ADMISSION_RECEIPT_PURPOSE,
    )
    batch.add_argument("--viewer-root", type=Path)
    batch.add_argument("--title", default="")

    explicit = commands.add_parser("assign")
    explicit.add_argument("--batch-id", required=True)
    explicit.add_argument("--task-id", required=True)
    explicit.add_argument("--expert-id", required=True)

    automatic = commands.add_parser("auto-assign")
    automatic.add_argument("--batch-id", required=True)

    publish = commands.add_parser("publish-batch")
    publish.add_argument("--batch-id", required=True)

    legacy = commands.add_parser("import-legacy-package")
    legacy.add_argument("--package-root", type=Path, required=True)
    legacy.add_argument("--no-activate", action="store_true")
    legacy.add_argument("--allow-historical-missing-checksums", action="store_true")

    package = commands.add_parser("build-package")
    package.add_argument("--batch-id", required=True)
    package.add_argument("--expert-id", required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--mode", choices=("standalone", "thin"), default="standalone")

    runtime = commands.add_parser("build-runtime")
    runtime.add_argument("--viewer-root", type=Path, required=True)
    runtime.add_argument("--output", type=Path, required=True)

    result_import = commands.add_parser("import-results")
    result_import.add_argument("--input", type=Path, required=True)

    result_export = commands.add_parser("export-results")
    result_export.add_argument("--format", choices=("json", "csv"), default="json")
    result_export.add_argument("--output", type=Path, required=True)

    commands.add_parser("status")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=4173)
    serve.add_argument("--no-browser", action="store_true")
    credentials = commands.add_parser("create-online-credentials")
    credentials.add_argument("--credentials", type=Path, required=True)
    credentials.add_argument("--plaintext", type=Path, required=True)
    credentials.add_argument("--session-secret", type=Path, required=True)
    credentials.add_argument("--admin-username", default="admin")
    list_users = commands.add_parser(
        "list-online-users", description="List non-sensitive online account metadata"
    )
    list_users.add_argument("--credentials", type=Path, required=True)
    online = commands.add_parser("serve-online")
    online.add_argument("--credentials", type=Path, required=True)
    online.add_argument("--session-secret", type=Path, required=True)
    online.add_argument("--batch-id")
    online.add_argument("--port", type=int, default=4174)
    online.add_argument("--base-path", default="/sv-annotate")
    online.add_argument("--insecure-cookie", action="store_true")
    online.add_argument("--review-bundles", type=Path)
    online.add_argument("--public-origin")
    online.add_argument("--review-public-origin")
    online.add_argument("--trajectory-root", type=Path)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "serve":
            from .server import serve

            return serve(args.db, port=args.port, open_browser=not args.no_browser)
        if args.command == "serve-online":
            from .online_server import serve_online

            return serve_online(
                args.db,
                credentials_path=args.credentials,
                session_secret_path=args.session_secret,
                batch_id=args.batch_id,
                port=args.port,
                base_path=args.base_path,
                secure_cookie=not args.insecure_cookie,
                review_bundles_path=args.review_bundles,
                public_origin=args.public_origin,
                review_public_origin=args.review_public_origin,
                trajectory_root=args.trajectory_root,
            )
        if args.command == "list-online-users":
            from .online_auth import list_online_users

            _write_json({"users": list_online_users(args.credentials)})
            return 0
        with ManagerStore(args.db) as store:
            if args.command == "init":
                result = {"database": str(args.db.resolve()), "initialized": True}
            elif args.command == "import-experts":
                result = store.import_experts_file(args.csv)
            elif args.command == "import-batch":
                result = store.import_batch(
                    batch_id=args.batch_id,
                    tasks_jsonl=args.tasks_jsonl,
                    asset_root=args.asset_root,
                    admission_receipt=args.admission_receipt,
                    admission_purpose=args.admission_purpose,
                    viewer_root=args.viewer_root,
                    title=args.title,
                )
            elif args.command == "assign":
                result = store.assign_explicit(
                    batch_id=args.batch_id, task_id=args.task_id, expert_id=args.expert_id
                )
            elif args.command == "auto-assign":
                result = store.auto_assign(batch_id=args.batch_id)
            elif args.command == "publish-batch":
                result = store.publish_batch(batch_id=args.batch_id)
            elif args.command == "import-legacy-package":
                result = store.import_legacy_package(
                    args.package_root,
                    activate=not args.no_activate,
                    integrity_policy=(
                        LEGACY_INTEGRITY_ALLOW_MISSING
                        if args.allow_historical_missing_checksums
                        else LEGACY_INTEGRITY_REQUIRE_CHECKSUMS
                    ),
                )
            elif args.command == "build-package":
                result = build_expert_package(
                    store,
                    batch_id=args.batch_id,
                    expert_id=args.expert_id,
                    output=args.output,
                    mode=args.mode,
                )
            elif args.command == "build-runtime":
                result = build_shared_runtime(
                    viewer_root=args.viewer_root,
                    output=args.output,
                )
            elif args.command == "import-results":
                result = store.import_submission(
                    json.loads(args.input.read_text(encoding="utf-8"))
                )
            elif args.command == "export-results":
                result = export_results(store, args.output, args.format)
            elif args.command == "status":
                result = store.overview()
            elif args.command == "create-online-credentials":
                from .online_auth import create_credentials

                result = create_credentials(
                    store,
                    credentials_path=args.credentials,
                    plaintext_path=args.plaintext,
                    session_secret_path=args.session_secret,
                    admin_username=args.admin_username,
                )
            else:
                raise AssertionError(args.command)
        _write_json(result)
        if args.command == "import-results" and result["rejected"]:
            return 2
        return 0
    except (ContractError, PreflightError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as error:
        payload = {"ok": False, "error": str(error)}
        if isinstance(error, PreflightError):
            payload["errors"] = error.errors
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
