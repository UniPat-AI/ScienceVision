#!/usr/bin/env python3
"""Build, verify, or serve a local pending-human-review bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qagen.reviewer_bundle import (
    build_pending_reviewer_bundle,
    create_pending_reviewer_bundle_server,
    verify_pending_reviewer_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a non-staged pending-review bundle")
    build.add_argument("--run-root", type=Path, required=True)
    build.add_argument("--scene-registry", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-only", help="verify a bundle without starting a viewer")
    verify.add_argument("--bundle", type=Path, required=True)
    serve = commands.add_parser("serve", help="serve a verified bundle on loopback")
    serve.add_argument("--bundle", type=Path, required=True)
    serve.add_argument("--port", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        print(
            json.dumps(
                build_pending_reviewer_bundle(
                    run_root=args.run_root,
                    scene_registry_path=args.scene_registry,
                    output_dir=args.output,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "verify-only":
        print(json.dumps(verify_pending_reviewer_bundle(args.bundle), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    server = create_pending_reviewer_bundle_server(args.bundle, port=args.port)
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
