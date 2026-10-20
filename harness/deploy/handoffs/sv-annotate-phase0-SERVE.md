# Expert Annotation Phase-0 — self-contained bundle

Static, self-contained. Serve the **bundle root** with any static server; every
page uses root-absolute paths (`/viewer/...`, `/tasks/...`, `/crawl_data/...`),
so it only works when served from this directory as web root.

## Serve

```bash
# from inside the extracted sv-annotate-phase0/ directory
python3 -m http.server 8080          # then open http://<host>:8080/
```

Or point Caddy/nginx at this directory as the site root.

## Entry sub-pages

- `/`                     — hub (links all tasks + replay)
- `/viewer/molstar.html?file=/crawl_data/biology/small_molecules_derived/2519_caffeine.mol&type=mol&annotate=<task_id>`
  - task_id ∈ { bio_caffeine_v2_topology, bio_caffeine_spatial_distance,
                bio_caffeine_v3_occlusion, bio_caffeine_v4_rotation_occlusion }
- `/viewer/replay.html`   — load a downloaded `<task_id>.bundle.json`

## Notes

- Phase-0 packaging is **browser-download** (no backend): Submit downloads the
  bundle locally; there is no upload endpoint.
- Molstar makes an optional call to an external volume-server
  (`molstarvolseg.ncbr.muni.cz`) that fails offline — harmless, unrelated to
  annotation.
- Without `?annotate=`, molstar.html is the unmodified plain viewer.
