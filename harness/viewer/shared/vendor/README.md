# viewer/shared/vendor

Mirror of third-party JS/CSS used by `viewer/*.html`. Populated by
`tools/sync_vendor.py`; do not hand-edit.

## Layout

```
viewer/shared/vendor/<lib>/<version>/<original-path>
```

`<lib>` and `<version>` are pinned in `tools/sync_vendor.py::VENDOR_SPEC`.
The path mirrors the upstream npm/cdn layout (`build/`, `dist/`,
`examples/jsm/...`) so import URLs differ from the original only in their
absolute prefix.

## Regenerate

```bash
python3 tools/sync_vendor.py            # idempotent — skips existing files
python3 tools/sync_vendor.py --check    # CI: nonzero if any file missing
```

## Why mirror?

1. Eliminate cross-origin DNS + TLS per viewer load (~100–300 ms on SSH
   tunnel).
2. Let nginx apply `Cache-Control: public, max-age=31536000, immutable`
   for hot re-visits (version is in the path → safe).
3. Survive `cdn.jsdelivr.net` / `unpkg.com` outages.

CDN fallback is intentionally **not** preserved — see spec §6.
