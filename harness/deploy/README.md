# BabyVision-v2 viewer deployment

This directory ships the **on-server runtime** for the static viewer site
under `../viewer/`. It is *not* needed to develop the viewer; it is what
runs it on a public host.

The viewer is fully static (HTML + bundled `three.js` 0.160, Mol*, NiiVue,
Cornerstone, MapLibre, …) so the server's only job is to hand back files.

Current live deploy: **http://47.84.143.121/** (HTTP only; see HTTPS
section to upgrade).

## What gets installed

| File in this dir                  | Installed to                              |
| --------------------------------- | ----------------------------------------- |
| `Caddyfile`                       | `/etc/caddy/Caddyfile`                    |
| `Caddyfile.https.example`         | (template; not installed)                 |
| `babyvision-viewer.service`       | `/etc/systemd/system/`                    |
| `babyvision-viewer.sudoers`       | `/etc/sudoers.d/babyvision-viewer`        |
| `install.sh`                      | (run-once installer; not installed)       |
| The Caddy v2 binary               | `/usr/local/bin/caddy`                    |
| Log dir                           | `/var/log/caddy/` (owned by `matianyi`)   |

The `Caddyfile`, `.service`, and `.sudoers` files in this directory are
the **source of truth**. Edit them here, re-run `install.sh`, and they
are re-deployed.

## Document root and security model

Caddy serves the **repository root** (`/home/matianyi/BabyVision-v2/`),
not just `viewer/`. The viewer's `docs.html` and data-loading paths use
absolute URLs like `/docs/platform/quickstart.md` and
`/data/assets.index.json` that assume the repo root is at `/`. Serving
only `viewer/` (with a `uri strip_prefix /viewer` rewrite) breaks all
those sibling-tree fetches.

A naked `/` redirects to `/viewer/` (302).

Because the repo root is exposed, the Caddyfile explicitly **404s**:

```
@hidden path_regexp ^/(\.|deploy($|/))
respond @hidden 404
```

Covers in one rule:

| Path                                        | Why hidden                                |
| ------------------------------------------- | ----------------------------------------- |
| `/.env`, `/.env.*`                          | secrets (ModelScope creds, etc.)          |
| `/.git`, `/.git/*`                          | repo internals, commit metadata           |
| `/.github/*`                                | workflows, may leak CI specifics          |
| `/.gitignore`, `/.mirror-include`           | not sensitive but pointless to expose     |
| `/.pytest_cache/*`, `/.ruff_cache/*`, etc.  | dev-tool noise                            |
| `/.venv/*`, `/.idea/*`, `/.vscode/*`        | local env                                 |
| `/deploy/*`                                 | **including this README and the sudoers fragment** |

We return 404, not 403, so we don't leak the existence of these paths.

## First-time install

You need `sudo` on the host **once**. As `matianyi`:

```bash
# 1. fetch the Caddy v2 binary into /tmp
curl -fsSL https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz \
  | tar -xz -C /tmp caddy && chmod +x /tmp/caddy

# 2. run the installer (idempotent — safe to re-run)
sudo bash /home/matianyi/BabyVision-v2/deploy/install.sh

# 3. populate the vendor cache (see next section)
python3 /home/matianyi/BabyVision-v2/tools/sync_vendor.py

# 4. populate the data cache (see "Filling crawl_data/")

# 5. verify
curl -fsI http://localhost/
../scripts/viewer status
```

Re-run `install.sh` after editing any file in this directory to push
changes to the live host.

## Day-to-day control

From anywhere on the box:

```bash
/home/matianyi/BabyVision-v2/scripts/viewer start
/home/matianyi/BabyVision-v2/scripts/viewer stop
/home/matianyi/BabyVision-v2/scripts/viewer restart
/home/matianyi/BabyVision-v2/scripts/viewer reload   # hot reload of Caddyfile changes
/home/matianyi/BabyVision-v2/scripts/viewer status
/home/matianyi/BabyVision-v2/scripts/viewer logs     # follow journal
```

`start | stop | restart | reload` do not prompt for a password — the
sudoers fragment whitelists exactly those subcommands for `matianyi` on
this single unit. `caddy reload` performs a zero-downtime config swap
via the loopback-bound admin API (`localhost:2019`).

## Authenticated expert task manager

The optional online task manager runs as a separate loopback service on
`127.0.0.1:4174`. Caddy proxies only `/sv-annotate/*` to it. Every request is
authenticated, experts can read only their own assignments and asset closure,
and the management console is restricted to the admin role.

Install `deploy/babyvision-task-manager.service` as
`/etc/systemd/system/babyvision-task-manager.service`, then enable the unit.
The service follows per-expert active bundle pointers in `manager.db`; an
administrator publishes a fully assigned batch from the management console
without restarting the service. Credentials,
`session.key`, and `manager.db` are runtime secrets/state and must never be
committed or served by Caddy.

The service's `--public-origin` must be the exact browser origin (scheme,
hostname, and port). Every state-changing request is rejected unless its
`Origin` header matches this value exactly; the loopback default is only for
local development.

When review bundles are enabled, also configure
`--review-public-origin https://review.babyvision-v2.unipatai.com` with a
separate HTTPS hostname and a reverse proxy to the bundle's loopback review
port (for example `127.0.0.1:4175`). This hostname is a deployment placeholder,
not evidence that DNS, TLS, or proxy routing already exists. Never serve review
bundle files from the manager origin or under `/sv-annotate`.

Mount or permission each imported `asset_root` read-only for the task-manager
service while any bundle from that batch is active. Publishing rechecks the
declared SHA-256 closure, while HTTP Range requests rely on that immutable root
and enforce only the published path closure, traversal checks, and symlink
rejection. Asset changes require a new batch and publish operation.

```bash
sudo install -o root -g root -m 0644 \
  deploy/babyvision-task-manager.service \
  /etc/systemd/system/babyvision-task-manager.service
sudo systemctl daemon-reload
sudo systemctl enable --now babyvision-task-manager
sudo install -o root -g root -m 0644 \
  deploy/Caddyfile.live-precompressed /etc/caddy/Caddyfile
sudo systemctl reload babyvision-viewer
```

The expert entry point is `/sv-annotate/`; authenticated administrators are
redirected to `/sv-annotate/admin/`. The service remains loopback-only, so
port 4174 must not be opened in the host firewall.

### Building a release artifact

The unit's `WorkingDirectory` points at an immutable, read-only release
directory rather than a git checkout, so upgrading the service means building a
new release and repointing the unit. `deploy/build_online_manager_release.py`
produces one:

```bash
python3 deploy/build_online_manager_release.py --out dist
# release_id  <git_head>-<artifact_sha256[:12]>
# dist/<release_id>/{release-manifest.json,SHA256SUMS,artifact.tar.gz}
```

The payload is an explicit allowlist (`tools/__init__.py`,
`tools/offline_task_manager/`, the two `deploy/` files the host installs,
`viewer/shared/task-screenshot.js`, `LICENSE`); tests, developer docs, CSV
fixtures and bytecode are excluded, and a denylist refuses to package anything
that looks like a database, key, or credentials file. The tarball is
byte-reproducible: entries are sorted, ownership is normalised, and both the tar
and gzip timestamps are pinned to the HEAD commit time, so the same commit and
worktree always yield the same `release_id`.

To deploy, extract the tarball into a fresh
`releases/<release_id>/app`, make it read-only, verify it against `SHA256SUMS`,
then repoint the unit:

```bash
tar xzf artifact.tar.gz --strip-components=1 -C releases/<release_id>/app
(cd releases/<release_id>/app && sha256sum -c SHA256SUMS)
sudo sed -i "s|releases/[^/]*/app|releases/<release_id>/app|" \
  /etc/systemd/system/babyvision-task-manager.service
sudo systemctl daemon-reload
sudo systemctl restart babyvision-task-manager
```

Keep the previous release directory on disk; rolling back is the same `sed` with
the old `release_id` plus a restart. Validate a release on a canary port first
(`--port 4175 --insecure-cookie` against a copy of `manager.db`) — the canary
needs no root, so only the final repoint requires `sudo`.

## Filling the vendor cache

The viewer pages import vendored copies of `three.js`, Mol\*, NiiVue,
Cornerstone, MapLibre, PMTiles, JSZip, DICOM-parser, etc. from
`viewer/shared/vendor/<lib>/<version>/...`. The version + path pins
live in `tools/sync_vendor.py::VENDOR_SPEC`.

**On a fresh host the vendor tree is empty.** Populate it once:

```bash
python3 /home/matianyi/BabyVision-v2/tools/sync_vendor.py            # idempotent
python3 /home/matianyi/BabyVision-v2/tools/sync_vendor.py --check    # CI gate: exit != 0 if any file missing
```

The script downloads each pinned file from its upstream CDN (jsDelivr /
unpkg) into the on-disk vendor layout. Total ~20 MB.

When the mirror tool (`tools/sync_from_sciencevision.py` driven by
`.mirror-include`) drops `tools/sync_vendor.py` or
`viewer/shared/vendor/**`, partners can no longer populate vendor.
`.mirror-include` now lists `tools/sync_vendor.py` explicitly to keep
the script alive.

## Filling the data cache (`crawl_data/`)

The viewer is parameterized: it loads models / molecules / DICOMs from
`?file=/crawl_data/<domain>/<bundle>/<file>`. Those bundles are
**deliberately not in git** — partners pull them separately. `.gitignore`
excludes `/crawl_data/`.

Drop bundles directly into `/home/matianyi/BabyVision-v2/crawl_data/`
and they are served immediately (no Caddy restart). The asset index at
`data/assets.index.json` already references concrete paths under
`/crawl_data/...`; match those filenames and the index entries light up.

For verification we ship a 25 MB public-domain starter pack:

| Path                                              | License       | Source                              |
| ------------------------------------------------- | ------------- | ----------------------------------- |
| `crawl_data/industrial/khronos_gltf/*.glb`        | CC0           | KhronosGroup/glTF-Sample-Assets     |
| `crawl_data/industrial/mesh_samples/threejs_walt_head.obj` | MIT (three.js) | mrdoob/three.js examples            |
| `crawl_data/industrial/mesh_samples/trimesh_bunny.ply` | Public (Stanford) | mikedh/trimesh models               |
| `crawl_data/biology/rcsb_pdb_extras/*.pdb`        | Public domain | files.rcsb.org                      |

Real production data is fetched per-partner; the manifest's `data` block
in `viewer/manifest.local.example.json` shows how to point at a separate
checked-out tree (e.g. `../../private-data/assets.index.json`).

## Where things live at runtime

- **Bound port: 80** (no 8080 hop). Non-root binding is granted by
  `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the systemd unit.
- **Document root: repository root** `/home/matianyi/BabyVision-v2/`
  (a `git pull` in the repo is immediately served — no copy step).
- **Access log:** `/var/log/caddy/babyvision-viewer.log` (rolled at
  50 MiB, 5 archived).
- **Journal:** `journalctl -u babyvision-viewer`.
- **Admin API:** `127.0.0.1:2019` (loopback only; used by `reload`).
- **Auto-start:** `systemctl enable` is run by `install.sh`; the unit
  comes back up on reboot.

## Verification

A clean install passes all three matrices below.

### Programmatic (curl)

```bash
probe() { code=$(curl -fso /dev/null -w '%{http_code}' -m 5 "http://localhost$1"); printf '%-3s  %s\n' "$code" "$1"; }

# Positive routing
for p in / /viewer/ /viewer/docs.html /viewer/gltf.html \
         /docs/platform/quickstart.md /data/assets.index.json \
         /viewer/manifest.json \
         /viewer/shared/vendor/three/0.160.0/build/three.module.js \
         /viewer/shared/vendor/molstar/4.5.0/build/viewer/molstar.js
do probe "$p"; done
# expect: / -> 302, everything else -> 200

# Security
for p in /.env /.env.production /.git /.git/HEAD /.gitignore \
         /.mirror-include /deploy /deploy/Caddyfile \
         /deploy/babyvision-viewer.sudoers
do probe "$p"; done
# expect: all 404

# Public-IP sanity
curl -fsI -m 5 http://47.84.143.121/viewer/docs.html | head -1   # -> 200
curl -fso /dev/null -w '%{http_code}\n' -m 5 http://47.84.143.121/.env  # -> 404
```

### Visual (browser)

WebGL output cannot be verified by `curl`. Open each URL — every one
should render an interactive 3D / 2D scene within a second on a normal
laptop.

| What it proves                  | URL                                                                                                   |
| ------------------------------- | ----------------------------------------------------------------------------------------------------- |
| three.js + GLTFLoader + PBR     | `/viewer/gltf.html?file=/crawl_data/industrial/khronos_gltf/DamagedHelmet.glb`                        |
| three.js + PLYLoader            | `/viewer/gltf.html?file=/crawl_data/industrial/mesh_samples/trimesh_bunny.ply`                        |
| Mol\* + PDB parser              | `/viewer/molstar.html?file=/crawl_data/biology/rcsb_pdb_extras/1MBN.pdb&type=pdb`                     |
| docs router + markdown render   | `/viewer/docs.html`                                                                                   |

Black canvas + console 404s = a path we missed. Blank page + no errors
= a viewer JS bug, not a deploy bug. Spinning page that never loads = a
vendor file is missing (re-run `sync_vendor.py`).

## Known pitfalls

Three non-typical traps from the initial bring-up; recorded so the next
operator doesn't re-discover them.

1. **`caddy validate` opens the log file as root.** When `install.sh`
   runs `caddy validate` under `sudo`, Caddy creates
   `/var/log/caddy/babyvision-viewer.log` as `root:root 0600`. The
   service then runs as `matianyi` and cannot write to it
   (`open ...: permission denied`). `install.sh` ends with
   `chown -R matianyi:matianyi /var/log/caddy` to fix this.

2. **`admin off` disables `caddy reload`.** Setting `admin off` in the
   Caddyfile turns off the loopback admin API at `:2019`. That API is
   exactly what `caddy reload` uses to perform zero-downtime config
   swaps, so `reload` would error with `connection refused`. Use
   `admin localhost:2019` (default; loopback only) instead.

3. **Doc root must be the repo root, not `viewer/`.** The viewer's
   docs router and data loaders fetch `/docs/*.md` and `/data/*.json`
   (absolute paths assuming repo root at `/`). Rooting on `viewer/` —
   even with a `uri strip_prefix /viewer` rewrite — 404s every doc
   page and every data manifest. The price is that `.env`, `.git/`,
   and `deploy/` (with its sudoers fragment) would be exposed without
   the explicit `@hidden` deny.

## Going to HTTPS

For a public-facing download site, HTTPS is **not optional**:

- It guarantees **integrity** (downloaders get the bytes you served, not
  a proxy-injected version).
- It guarantees **authenticity** (visitors are talking to your box, not
  a hijack).
- It unlocks **secure-context APIs** the viewer relies on (WebGL2
  extensions, WebGPU, clipboard, future WebXR), which browsers gate
  behind HTTPS.

The current HTTP-on-80 setup is a temporary stage. Switch as soon as you
have a hostname.

### One-step migration

```bash
# 1. Get a hostname pointed at 47.84.143.121:
#    - a real domain (preferred), OR
#    - a free DDNS host (DuckDNS, no-ip, Cloudflare-hosted) — Let's Encrypt
#      treats DDNS hostnames the same as registered domains.

# 2. Drop in the HTTPS Caddyfile and edit the hostname:
sudo cp /home/matianyi/BabyVision-v2/deploy/Caddyfile.https.example /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile     # replace HOSTNAME with yours

# 3. Reload — Caddy obtains a Let's Encrypt cert on first request via
#    ACME HTTP-01 and starts serving 443. 80 auto-redirects to 443.
/home/matianyi/BabyVision-v2/scripts/viewer reload
```

`Caddyfile.https.example` already covers:

- HTTP/2 + HTTP/3 (QUIC) on 443
- modern TLS only (1.2 + 1.3)
- HSTS header
- automatic cert renewal ~30 days before expiry
- `Cache-Control: public, max-age=3600` for downloads
- `80 -> 443` redirect (Caddy's default when you use a domain form)

No `certbot`, no cron job.

### Don't forget to port the `@hidden` block

The HTTPS Caddyfile must keep the `@hidden path_regexp ...` block from
the HTTP version — otherwise switching to HTTPS would re-expose
`.env`, `.git/`, and `deploy/`.
