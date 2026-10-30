"""Minimal http server for the ScienceVision viewer.

- Serves this directory on 127.0.0.1:8080
- Returns 404 for any path containing a dot-file segment (.env, .git, .pytest_cache, etc.)
- Suppresses directory listings (returns 404 if no index.html in that dir)

Use:
    python3 _dev_serve.py
Then open: http://127.0.0.1:8080/viewer/
"""
import http.server
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8080

class SafeHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        resolved = super().translate_path(path)
        try:
            rel = Path(resolved).resolve().relative_to(ROOT)
        except (ValueError, OSError):
            return "/dev/null/forbidden"
        for part in rel.parts:
            if part.startswith("."):
                return "/dev/null/forbidden"
        return resolved

    def list_directory(self, path):
        self.send_error(404, "Directory listing disabled")
        return None


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), SafeHandler) as httpd:
    print(f"serving {ROOT} at http://127.0.0.1:{PORT}/", flush=True)
    httpd.serve_forever()
