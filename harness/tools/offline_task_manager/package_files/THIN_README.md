# ScienceVision thin expert bundle

This directory contains one immutable expert task bundle and its exact assets.
It intentionally does not contain the ScienceVision expert app or viewer runtime.

Ask the administrator for the shared runtime directory, then verify and open the bundle:

```bash
python3 serve_bundle.py --runtime /absolute/path/to/sciencevision-runtime --verify-only
python3 serve_bundle.py --runtime /absolute/path/to/sciencevision-runtime
```

The review workspace opens at `http://127.0.0.1:4180/`. Keep using the same port so
browser-local drafts remain available. Use `--port 4181` when 4180 is occupied.

Do not edit `bundle_manifest.json`, `tasks/`, assets, `SHA256SUMS`, or the shared
runtime. The launcher rejects a runtime whose loader API, renderers, or
capabilities do not satisfy `bundle_manifest.json`. Return the downloaded
`.results.json` file to the administrator.
