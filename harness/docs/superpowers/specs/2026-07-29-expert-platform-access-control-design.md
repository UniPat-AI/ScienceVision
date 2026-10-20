# Expert Platform Access Control + Anti-Trace (Phase 2)

**Date:** 2026-07-29
**Status:** Design approved, pending spec review
**Scope:** Gate the expert annotation platform (`/sv-annotate/`) behind
per-expert tokens, persist annotations server-side, and harden against
tracing. The public site, QA display, and all Phase-1 work are unchanged.

## Context

`babyvision-v2.unipatai.com` is a static site served by Caddy on the Sing
server (`47.84.143.121`), docroot `~/BabyVision-v2/`. Caddy runs as the
`babyvision-viewer.service` systemd unit (user `matianyi`, config
`/etc/caddy/Caddyfile`; edits require `sudo`). Sing has Python 3.14 (stdlib
`sqlite3` included), Caddy v2.11.4. No `sqlite3` CLI, but the Python module
is built in. FastAPI is present; flask is not.

Today `/sv-annotate/` is served as an open static directory — anyone with the
URL can open it. Worse, the annotation shell fetches the raw task JSONL
directly from `/QA-Gen/tasks/<set>/ScienceVision_<set>_final_tasks.jsonl`, and
those records **contain the answers**: `ground_truth` (reference answer +
rationale) and `expert_review.answer_after` (e.g. `dihedral_deg: 60`,
`structure: hippocampus`, `boundary_slice_range: [29,32]`). Any expert can
read the answer from the browser Network tab. The same JSONL legitimately
feeds the public QA *display* (`viewer/sciencevision_tasks.html`), which is
meant to show `expert_review` — so the two consumers need different data.

## Requirements

1. **Access gating:** the annotation platform is reachable only with a valid
   per-expert token/URL; everyone else cannot access or discover it.
2. **Anti-trace (三条):**
   - a. Token/URL is undiscoverable and unindexed (not crawled, not in
     directory listings, not enumerable, not logged).
   - b. Token does not leak via HTTP Referer or the address bar.
   - c. Experts cannot reverse-engineer the answer / ground truth from the
     page or its network traffic.
3. **Per-expert tokens:** admin issues a named token per expert; revocable;
   optional expiry. Submissions record which expert.
4. **Server-side persistence:** annotation submissions are written to SQLite
   on Sing (replacing the current download-a-bundle behavior).

## Decisions (from brainstorming)

- **Mechanism:** small localhost-only Python service behind Caddy
  (`reverse_proxy`), **stdlib only** (`http.server` + `sqlite3`) — no pip
  installs, no ASGI server.
- **Token model:** per-expert, named, revocable, optional expiry.
- **Persistence:** server-side SQLite; no download bundle.
- **Deploy split:** all code + a one-shot install script + the edited
  Caddyfile are prepared and rsynced by the developer; the user runs the
  `sudo` steps (systemd install + `caddy reload`) on Sing.

## Architecture

```
Browser ──TLS──> Caddy ──┬─ /  ...static... (public, unchanged)
                         ├─ /sv-annotate/*   ─> 127.0.0.1:8787  (gated)
                         └─ /sv-auth/api/*    ─> 127.0.0.1:8787  (gated API)
                              │
                              └─ sv-auth (Python stdlib) ── SQLite
                                                         └─ redacted task feed
```

Caddy remains the TLS/static front and keeps serving the entire public site
directly. It `reverse_proxy`es exactly two path groups to the localhost
service; the service is the gatekeeper for `/sv-annotate/` (it decides whether
to hand out the static assets) and owns the task/submission API. The service
binds `127.0.0.1:8787` only (never public); Caddy is the sole external
listener.

## Auth flow (capability URL → session cookie)

1. Admin issues a per-expert token via CLI. Expert receives
   `https://babyvision-v2.unipatai.com/sv-annotate/?t=<opaque-256bit>`.
2. First load: the service validates `?t=`. If valid, it sets an
   **HttpOnly + Secure + SameSite=Strict** session cookie and **302-redirects
   to the clean `/sv-annotate/`** (no query string) — the token leaves the
   address bar, browser history, Referer, and any screenshot.
3. Subsequent requests are authorized by the cookie; no token in any later URL.
4. Missing/invalid/revoked/expired token **and** no valid session cookie →
   **HTTP 404** with `noindex` (a 404, not 401, avoids confirming the platform
   exists → anti-discovery).

Session cookie value is random; only its SHA-256 hash is stored. Sessions have
their own expiry and are re-checked against live token state on every request,
so revoking a token invalidates its live sessions on their next request.

## Anti-trace measures (mapped to requirement 2)

- **2a undiscoverable/unindexed:**
  - `X-Robots-Tag: noindex, nofollow` header on all gated responses.
  - `robots.txt` (public) disallows `/sv-annotate/`.
  - Unauthorized access returns 404, not 401/403.
  - Tokens are 256-bit URL-safe random — unguessable, non-enumerable.
  - The service never logs the token or the `?t=` query (stripped from any
    log line before writing).
- **2b no Referer leak:**
  - `Referrer-Policy: no-referrer` header on every gated page and asset.
  - Token stripped from the URL after step 2 (302 to clean path).
  - All outbound links in the shell carry `rel="noopener noreferrer"`.
- **2c no answer reverse-engineering:**
  - The service serves a **redacted** task feed: `ground_truth`,
    `expert_review`, `scorer`, and `quality_gates` are removed from each
    record server-side before it reaches the client.
  - The raw answer-bearing JSONL is **not reachable** under `/sv-annotate/`
    (the shell no longer fetches `/QA-Gen/tasks/...`; it calls the API).
  - Redaction is **fail-closed**: a record that cannot be parsed/redacted is
    omitted from the feed, never served raw.
  - The public QA display (`sciencevision_tasks.html`) is unchanged and still
    shows `expert_review` — it is a different, intentionally-public consumer.

## Components (units and boundaries)

- `deploy/sv-auth/app.py` — stdlib `ThreadingHTTPServer` handler: routing,
  cookie/token authorization, static passthrough for `/sv-annotate/`, redacted
  task API, submission API, security headers. Depends on `db.py`, `redact.py`.
- `deploy/sv-auth/db.py` — SQLite connection + schema init + typed queries for
  experts, tokens, sessions, submissions. Single source of DB truth.
- `deploy/sv-auth/redact.py` — pure function `redact(record) -> record`
  stripping answer-bearing fields; unit-tested in isolation.
- `deploy/sv-auth/manage_tokens.py` — admin CLI: `create --expert "<name>"
  [--expires <ISO8601>]` (prints the one-time URL), `list`, `revoke <token_id>`.
- `deploy/sv-auth/sv-auth.service` — systemd unit modeled on
  `babyvision-viewer.service` hardening (NoNewPrivileges, ProtectSystem,
  ReadWritePaths for the DB dir), `User=matianyi`, binds localhost.
- `deploy/sv-auth/install.sh` — one-shot: copy service file, `daemon-reload`,
  enable+start, print status. (User runs with `sudo`.)
- `deploy/Caddyfile` (+ live `/etc/caddy/Caddyfile`) — add `reverse_proxy`
  blocks for `/sv-annotate/*` and `/sv-auth/api/*` to `127.0.0.1:8787`; keep
  the existing `@hidden` and `/` rewrite. Live edit + reload is a `sudo` step.
- `sv-annotate/index.html` + `sv-annotate/viewer/shared/capture/annotate-shell.js`
  — fetch tasks from `/sv-auth/api/tasks?set=<set>` (redacted) instead of the
  raw JSONL; submit to `POST /sv-auth/api/submit` instead of downloading a
  bundle.
- `deploy/sv-auth/robots.txt` note — ensure `robots.txt` served at site root
  disallows `/sv-annotate/`.

## Data model (SQLite)

```sql
CREATE TABLE experts (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  note       TEXT,
  created_at TEXT NOT NULL              -- ISO8601 UTC
);
CREATE TABLE tokens (
  id         INTEGER PRIMARY KEY,
  expert_id  INTEGER NOT NULL REFERENCES experts(id),
  token_hash TEXT NOT NULL UNIQUE,      -- SHA-256(token); plaintext never stored
  created_at TEXT NOT NULL,
  expires_at TEXT,                       -- nullable = no expiry
  revoked_at TEXT                        -- nullable = active
);
CREATE TABLE sessions (
  id          INTEGER PRIMARY KEY,
  token_id    INTEGER NOT NULL REFERENCES tokens(id),
  cookie_hash TEXT NOT NULL UNIQUE,      -- SHA-256(cookie value)
  created_at  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  expires_at  TEXT NOT NULL
);
CREATE TABLE submissions (
  id              INTEGER PRIMARY KEY,
  task_id         TEXT NOT NULL,
  set_name        TEXT NOT NULL,
  expert_id       INTEGER NOT NULL REFERENCES experts(id),
  answer_json     TEXT NOT NULL,         -- expert's answer payload
  checkpoints_json TEXT,                 -- camera/view checkpoints
  screenshot_ref  TEXT,                  -- path/blob ref if captured
  created_at      TEXT NOT NULL
);
```

DB lives at `~/sv-auth/annotations.db` on Sing (outside docroot).

## API (all under `/sv-auth/api/`, gated identically to `/sv-annotate/`)

- `GET /sv-auth/api/tasks?set=<set>` → `{ tasks: [<redacted record>, ...] }`.
  404 if unauthorized; 400 for unknown set; answer fields never present.
- `POST /sv-auth/api/submit` (JSON body `{task_id, set, answer, checkpoints,
  screenshot?}`) → `{ ok: true, id }`. Records `expert_id` from the session.
  On DB failure returns `{ ok:false, error }` with 5xx; client retains the
  answer for retry.
- `GET /sv-auth/api/whoami` → `{ expert: "<name>" }` for the shell header
  (attribution display). 404 if unauthorized.

## Error handling / edge cases

- Invalid/expired/revoked token or missing cookie → 404 + `noindex`.
- Cookie valid but its token later revoked/expired → next request 404 (live
  re-check).
- Service down → Caddy returns 502 for the gated paths; the public site is
  unaffected (Caddy serves it directly).
- Redaction failure on a record → that record is omitted, not served raw
  (fail-closed).
- Submission DB write failure → API 5xx + `{ok:false}`; client keeps the
  answer in memory and surfaces a retry, so no silent data loss.
- Concurrent submissions → `ThreadingHTTPServer` with a per-request SQLite
  connection (or a serialized writer); WAL mode enabled.

## Testing / verification

- **Unit (run on the aliyun box, stdlib `unittest`):**
  - `redact()` removes every answer-bearing field (`ground_truth`,
    `expert_review`, `scorer`, `quality_gates`) and preserves the rest;
    asserts none survive across all three real expert-v2 sets.
  - token create → hash stored (not plaintext) → verify matches; expired and
    revoked tokens fail verification.
  - session issue → cookie hash stored → authorize; revoking the token fails
    subsequent authorization.
- **Integration (service running locally):**
  - no token, no cookie → 404 with `X-Robots-Tag`.
  - valid `?t=` → 302 to clean `/sv-annotate/` + `Set-Cookie`
    (HttpOnly, Secure, SameSite=Strict).
  - cookie → 200; `/sv-auth/api/tasks` payload contains zero answer fields.
  - `POST /submit` → row in SQLite with correct `expert_id`.
  - revoked token's existing cookie → 404 on next request.
- **Live smoke (after Sing deploy):**
  - `curl -I` shows `Referrer-Policy: no-referrer` and
    `X-Robots-Tag: noindex` on `/sv-annotate/`.
  - `curl /robots.txt` disallows `/sv-annotate/`.
  - public pages (`/`, `/qagen.html`, `/assets.html`) still 200 and unchanged.
  - a real issued token opens the platform; tasks load without answers;
    submit persists.

## Deploy plan

1. Developer rsyncs `deploy/sv-auth/**` and the edited `deploy/Caddyfile` to
   Sing (working-tree style, code paths only).
2. User runs (with `sudo`) on Sing:
   - `deploy/sv-auth/install.sh` (installs + starts `sv-auth.service`).
   - copies the Caddyfile changes into `/etc/caddy/Caddyfile` and
     `caddy reload` (or `sudo systemctl reload babyvision-viewer`).
3. User issues tokens with `manage_tokens.py create --expert "<name>"` and
   distributes the one-time URLs out-of-band.
4. Developer runs the live smoke checks.

## Out of scope

- Any change to the public site, QA display, or Phase-1 annotation viewers
  beyond repointing the shell's fetch/submit to the gated API.
- Rate limiting, IP allowlists, 2FA, email delivery of tokens (tokens are
  distributed out-of-band by the admin).
- Editing/exporting stored submissions (a later admin-review surface).
