# ScienceVision expert package

This package contains only the tasks assigned to one stable `expert_id`, their
declared asset-hash closure, and the complete shared offline viewer runtime.

Open a terminal in this extracted package folder (the folder containing this
README and `serve_package.py`), then run:

```bash
python3 serve_package.py --verify-only
python3 serve_package.py
```

The launcher resolves every package file relative to its own location, so no
repository, task-source, viewer, or asset path needs to be configured. By
default it opens `http://127.0.0.1:4180/`. Keep using that exact URL on later
runs: browser drafts are stored per origin (scheme, host, and port), so changing
the port makes drafts saved under the previous origin appear unavailable.

If port `4180` is already occupied, choose another stable port and reuse it on
every run, for example `python3 serve_package.py --port 4181`. `--port 0` is
available for tests or one-off sessions, but it chooses a new random port on
each launch and therefore must not be used for persistent expert work.

The checksummed package manifest includes the passed QC-admission provenance
that binds this task file and source-revision set. The manifest, redacted tasks,
assets, and viewer are the immutable input layer. Drafts live separately in
browser storage, and exported JSON files are append-only output artifacts;
neither changes package files.

Use **Export results** to create the single JSON file that the manager imports.
Trajectory capture is not yet implemented, so each result carries the explicit
`sciencevision.trajectory_ref.v1` `not_collected` reference.
