"""Standalone command interface for local reviewer bundle build, verify, and serve."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bundle import build_reviewer_bundle, verify_reviewer_bundle
from .server import create_reviewer_bundle_server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a local-only reviewer bundle")
    build.add_argument("--batch-manifest", type=Path, required=True)
    build.add_argument("--batch-plan", type=Path, required=True)
    build.add_argument("--staging-root", type=Path, required=True)
    build.add_argument("--asset-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--viewer-runtime-root", type=Path)
    build.add_argument("--copy-assets", action="store_true", help="copy selected bytes instead of hard-linking when possible")
    build.add_argument("--allow-partial", action="store_true", help="allow an explicitly incomplete local review package")
    verify = commands.add_parser("verify-only", help="verify a bundle without starting an HTTP server")
    verify.add_argument("--bundle", type=Path, required=True)
    serve = commands.add_parser("serve", help="serve a verified bundle on the loopback interface")
    serve.add_argument("--bundle", type=Path, required=True)
    serve.add_argument("--port", type=int, default=8788)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        result = build_reviewer_bundle(
            batch_manifest_path=args.batch_manifest,
            batch_plan_path=args.batch_plan,
            staging_root=args.staging_root,
            asset_root=args.asset_root,
            output_dir=args.output,
            viewer_runtime_root=args.viewer_runtime_root,
            hardlink_assets=not args.copy_assets,
            allow_partial=args.allow_partial,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "verify-only":
        result = verify_reviewer_bundle(args.bundle)
        print(json.dumps({key: value for key, value in result.items() if key not in {"registry", "checksums"}}, indent=2, sort_keys=True))
        return 0
    server = create_reviewer_bundle_server(args.bundle, port=args.port)
    host, port = server.server_address[:2]
    print(f"http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
