#!/usr/bin/env python3
"""Loopback server that serves the repo viewer tree the way the expert page sees it.

Reuses online_server's MCP injection functions and STATIC_FILE_ROUTES so the
viewer page loaded here has the same `window.ScienceVisionViewerMCP` /
`ScienceVisionViewerScreenshot` halves as production. Assets are served from a
local directory (downloaded read-only from the server's batch asset_root).

    python3 showcase_viewer_server.py --repo <repo> --assets <dir> --port 4181
"""

from __future__ import annotations

import argparse
import http.server
import mimetypes
import sys
from pathlib import Path

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".mjs")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--assets", required=True, type=Path)
    ap.add_argument("--port", type=int, default=4181)
    args = ap.parse_args()
    sys.path.insert(0, str(args.repo))
    from tools.offline_task_manager import online_server as srv

    viewer_root = args.repo / "viewer"
    assets_root = args.assets.resolve()
    injectors = {
        "bim_coordination.html": srv._inject_bim_mcp_runtime,
        "architecture_inspection.html": srv._inject_architecture_inspection_mcp_runtime,
        "molstar.html": srv._inject_molstar_mcp_runtime,
        "niivue.html": srv._inject_niivue_mcp_runtime,
        "occt.html": srv._inject_occt_mcp_runtime,
        "gltf.html": srv._inject_gltf_mcp_runtime,
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):  # quiet
            pass

        def _send(self, payload: bytes, ctype: str, status=200, extra=None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path.startswith("/static/mcp/"):
                target = srv.STATIC_FILE_ROUTES.get(path)
                if target is None or not Path(target).is_file():
                    return self.send_error(404)
                return self._send(Path(target).read_bytes(), "text/javascript")
            if path.startswith("/viewer/"):
                rel = path[len("/viewer/"):]
                file = (viewer_root / rel).resolve()
                if not file.is_relative_to(viewer_root) or not file.is_file():
                    return self.send_error(404)
                payload = file.read_bytes()
                if file.suffix == ".html" and file.name in injectors:
                    payload = injectors[file.name](payload, "")
                ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                return self._send(payload, ctype)
            if path.startswith("/assets/"):
                file = (assets_root / path.lstrip("/")).resolve()
                if not file.is_relative_to(assets_root) or not file.is_file():
                    return self.send_error(404)
                data = file.read_bytes()
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a or 0); end = int(b) if b else len(data) - 1
                    chunk = data[start:end + 1]
                    return self._send(chunk, "application/octet-stream", 206, {
                        "Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"})
                return self._send(data, "application/octet-stream", 200, {"Accept-Ranges": "bytes"})
            return self.send_error(404)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving on http://127.0.0.1:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
