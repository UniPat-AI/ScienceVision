# ScienceVision offline task management

This manager imports stable expert identities and admitted immutable task
batches, assigns tasks by domain, atomically publishes one frozen task bundle
per expert, and validates append-only results. Online experts share one viewer
runtime; offline delivery can either reuse one installed runtime or remain a
self-contained legacy-compatible package.

## Exchange contract

Every join uses machine IDs; a person's name is display text and is never a
primary key.

| Object | Stable fields |
|---|---|
| Expert CSV | `schema_version`, `expert_id`, `name`, `domains` |
| QC admission | `purpose`, exact task-file binding, source-revision set, policy hash |
| Task batch | `batch_id`, `task_id`, `task_revision_sha256` |
| Assignment | `assignment_id`, `expert_id`, task identity and revision |
| Expert task bundle | `bundle_id`, admission digest, redacted task and asset closure |
| Viewer runtime | `runtime_id`, loader API and exact file inventory |
| Expert package | `package_id`, `manifest_sha256`, assignment closure |
| Result | bundle or package/expert/assignment/task/revision, `attempt` |
| Submission | `submission_id`, `payload_sha256`, results |

Schemas are in [`schemas/`](schemas). `experts.csv` must use
`sciencevision.experts_csv.v1` and provide an explicit `expert_id`:

```csv
schema_version,expert_id,name,domains
sciencevision.experts_csv.v1,expert_bio_001,Dr. Chen,biology
sciencevision.experts_csv.v1,expert_med_001,Dr. Lin,medical;biology
sciencevision.experts_csv.v1,expert_chem_001,Dr. Rao,chemistry
```

Task input is JSONL. Each row requires `task_id`, `domain`, and a declared
`asset_hashes` closure containing `repo_path`, SHA-256, and optional byte size.
The matching files must exist below `--asset-root`. The manager computes the
task revision over the complete source row. A batch is immutable after import;
changed tasks require a new `batch_id`, while old batches and results remain.

Every import also requires the ScienceVision-generated
`sciencevision.qa_export_admission_receipt.v1` for that exact JSONL. The
manager verifies the receipt self-digest, `passed` status,
`expert_review_distribution` purpose, file SHA/count, and the direct rows'
top-level `source_revision_sha256` revision set before writing the batch. It
never unfolds nested `subquestions` from physical sample wrappers. It
deliberately does not reproduce ScienceVision's QC policy:
`source_admission`, QC report hashes, and policy hashes stay producer-owned.
Missing, rejected, purpose-mismatched, or drifted receipts fail closed for all
new work. Existing v1 packages can be imported only through the explicit
read-only legacy adapter. That adapter reports `legacy_pre_admission` and never
fabricates a v2 admission decision.

## Real workflow

From the repository root:

```bash
DB=/absolute/path/to/sciencevision-task-manager/tasks.db
SOURCE=/absolute/path/to/scale-v2.1-expert-review-candidate823-20260810

python3 -m tools.offline_task_manager --db "$DB" init
python3 -m tools.offline_task_manager --db "$DB" import-experts \
  --csv /absolute/path/to/experts.csv
python3 -m tools.offline_task_manager --db "$DB" import-batch \
  --batch-id scale-v2.1-candidate823-r1 \
  --title "scale-v2.1 candidate expert review" \
  --tasks-jsonl "$SOURCE/tasks/candidate_tasks.jsonl" \
  --asset-root "$SOURCE" \
  --admission-receipt "$SOURCE/qa_export_admission_receipt.json" \
  --admission-purpose expert_review_distribution
python3 -m tools.offline_task_manager --db "$DB" auto-assign \
  --batch-id scale-v2.1-candidate823-r1
python3 -m tools.offline_task_manager --db "$DB" publish-batch \
  --batch-id scale-v2.1-candidate823-r1
python3 -m tools.offline_task_manager --db "$DB" status
```

The default viewer root is `$SOURCE/viewer`. It must be self-contained and have
the Mol*/NiiVue screenshot provider; executable CDN/network imports fail
preflight.

`publish-batch` prepares every expert bundle first, then changes all affected
active pointers in one transaction. Any validation or write failure leaves the
previous active bundles unchanged. Preparation rechecks every declared asset
SHA-256 and verifies that the actual viewer files provide every required loader,
renderer, and screenshot/review capability, so source or runtime drift after
import blocks publication.

For offline review, install the shared runtime once and create small thin
bundles for each expert:

```bash
python3 -m tools.offline_task_manager --db "$DB" build-runtime \
  --viewer-root "$SOURCE/viewer" \
  --output /absolute/path/to/sciencevision-runtime
python3 -m tools.offline_task_manager --db "$DB" build-package \
  --batch-id scale-v2.1-candidate823-r1 \
  --expert-id expert_bio_001 \
  --mode thin \
  --output /absolute/path/to/bundles/expert_bio_001-r1
```

The thin bundle contains only that expert's frozen manifest, redacted tasks,
exact assets, checksums, and launcher. It does not copy the app or `viewer/`.
Building it also registers the prepared v2 bundle so its returned submission is
importable, but does not change that expert's active online bundle. A database
registration failure removes the just-published output directory.
Source task rows are projected onto an expert-visible allowlist; ground truth,
asset-grounding answers, scorers, oracle/source receipts, QC, quarantine, and
internal validation data cannot enter `tasks/tasks.jsonl`. No whole 5.58 GB
asset catalog is copied.

The expert runs:

```bash
cd /absolute/path/to/bundles/expert_bio_001-r1
python3 serve_bundle.py --runtime /absolute/path/to/sciencevision-runtime --verify-only
python3 serve_bundle.py --runtime /absolute/path/to/sciencevision-runtime
```

The launcher verifies both immutable inventories and mounts them at one local
origin. It opens
`http://127.0.0.1:4180/` by default. Drafts are persisted in browser storage
after every edit, and browser storage is isolated by the exact origin (scheme,
host, and port). Experts should therefore keep using port `4180`. If that port
is occupied, use a stable alternative such as `--port 4181` on every run.
`--port 0` remains available for tests or one-off sessions, but selects a new
random port on each launch and can make earlier drafts appear unavailable.

For a recipient who cannot install a shared runtime, use `--mode standalone`.
This preserves the original v1 package layout and `serve_package.py` workflow.

Legacy v1 import requires a complete, matching `SHA256SUMS` inventory by
default. For the nine audited packages created before checksums were introduced,
the operator must explicitly add `--allow-historical-missing-checksums`; this
compatibility policy still permits only the exact historical
`/tasks/tasks.jsonl` spelling and never relaxes traversal, containment, asset
digest, or symlink checks.

**Export results** downloads one checksummed JSON submission containing
separate task-validation feedback, answer, and reasoning. Screenshot
crop/copy/download remains local; the result contract reserves `evidence`.
Until a collector is implemented, trajectory is exactly:

```json
{"schema_version":"sciencevision.trajectory_ref.v1","status":"not_collected","reason":"collector_not_implemented"}
```

Load and consolidate returned files:

```bash
python3 -m tools.offline_task_manager --db "$DB" import-results \
  --input /absolute/path/to/pkg_sub.results.json
python3 -m tools.offline_task_manager --db "$DB" export-results \
  --format csv --output /absolute/path/to/exports/results-20260813.csv
```

Imports reject unknown packages/assignments, wrong experts, task revision
drift, schema drift, hash mismatch, and changed content for an existing
`submission_id` or `assignment_id + attempt`. Reimporting the same ID and hash
is reported as unchanged. Exports refuse to overwrite files and CSV cells are
escaped against spreadsheet formula injection. JSON exports use
`sciencevision.manager_results_export.v2` and explicitly declare the mixed v1
package / v2 bundle result exchange; CLI, local UI, and online UI share the same
encoder and fixed CSV columns.

## Local Web UI

```bash
python3 -m tools.offline_task_manager --db "$DB" serve --port 4173
```

Open `http://127.0.0.1:4173`. The server has no non-loopback bind option. Its
Setup, Distribution, and Results views expose the same preflight and validation
rules as the CLI. After assignment, choose the batch under **Publish to all
experts**. The page reports either the number activated or that the prior active
set was preserved. **Active expert bundles** is the operator view of what each
expert will receive. The same page builds a shared runtime, thin or standalone
delivery, and imports an existing v1 directory through the legacy adapter.

The immutable input layer is the checksummed package manifest, tasks, assets,
and viewer runtime. Mutable drafts live only in browser storage, and returned
submissions/manager exports are separate append-only output artifacts.

## Authenticated online gateway

`serve-online` is intended for a trusted Caddy reverse proxy. It still binds
only to loopback, but adds signed sessions, per-role authorization, CSRF
protection, per-expert assignment and asset isolation, HTTP range responses
for large 3D assets, and direct validated result intake.

Create each credential set once. The plaintext CSV is its one-time operator
handoff and must be kept outside the repository with mode `0600`:

```bash
python3 -m tools.offline_task_manager --db "$DB" create-online-credentials \
  --credentials /secure/state/credentials.json \
  --plaintext /secure/state/account-handoff.csv \
  --session-secret /secure/state/session.key

python3 -m tools.offline_task_manager list-online-users \
  --credentials /secure/state/credentials.json

python3 -m tools.offline_task_manager --db "$DB" serve-online \
  --credentials /secure/state/credentials.json \
  --session-secret /secure/state/session.key \
  --public-origin https://babyvision-v2.unipatai.com \
  --port 4174 --base-path /sv-annotate
```

There is no self-registration endpoint. Administrators create roster-backed
expert invitations in the manager UI. Every generated expert password is
temporary: it can establish only a restricted password-activation session and
cannot open tasks, viewer files, or submissions. The expert must choose a new
16-256 character password before receiving a full session. Administrators can
reissue a temporary password; doing so revokes all existing sessions for that
expert. Invite and reissue responses show the temporary password once and
never persist it in the credential file, browser storage, or manager operation
log. Existing v1 credential records without activation fields remain active.

Administrators can also open checksummed static review bundles without importing
them into the manager database. Pass a private, persistent configuration file:

```json
{
  "schema_version": "sciencevision.online_review_bundles.v1",
  "bundles": [
    {
      "review_id": "sample165",
      "title": "Expert v2 + Scale v1 sample 165",
      "root": "/absolute/path/to/expert-v2-scale-v1-sample165",
      "port": 4175
    }
  ]
}
```

```bash
python3 -m tools.offline_task_manager --db "$DB" serve-online \
  --credentials /secure/state/credentials.json \
  --session-secret /secure/state/session.key \
  --review-bundles /secure/state/review-bundles.json \
  --public-origin https://babyvision-v2.unipatai.com \
  --review-public-origin https://review.babyvision-v2.unipatai.com \
  --port 4174 --base-path /sv-annotate
```

The gateway verifies the bundle's complete `SHA256SUMS` inventory before it
starts, then owns a separate read-only loopback server for each review bundle.
Bundle HTML and JavaScript never execute on the manager origin, and the review
server exposes no write API or CORS permission. In a public deployment,
`--review-public-origin` is required whenever review bundles are enabled: it
must be a distinct HTTPS hostname from `--public-origin` and its reverse proxy
must reach the corresponding loopback review server. The
`https://review.babyvision-v2.unipatai.com` value above is only a placeholder;
it does not assert that DNS, TLS, or proxy routing has been configured. Do not
proxy a review bundle through the manager origin or under `/sv-annotate`.

For local development, omit `--review-public-origin` only when the manager
public origin is the actual `http://127.0.0.1:<manager-port>` binding. A review
bundle configured with `"port": 4175` then opens at
`http://127.0.0.1:4175`; `port: 0` selects an ephemeral loopback port.
The public-review MVP accepts one review bundle for one configured public review
origin. Multiple public bundles require a per-bundle origin or router and are
rejected at startup; local development without `--review-public-origin` may
continue to use independent loopback URLs for multiple bundles.
Review files are not anonymously reachable: an administrator's **Open viewer**
action creates a 60-second, single-use capability that becomes an isolated,
eight-hour read-only review session. The capability and review cookie are bound
to the issuing active administrator session, are revalidated on every review
request, and are revoked when that administrator logs out. The review cookie is
host-only (no `Domain` attribute). These review sources deliberately remain
outside manager task, assignment, result, and admission state. Drafts and
exports stay in the local browser; central submission is unavailable.

`list-online-users` prints only `username`, `role`, `expert_id`, and
`display_name`; it never prints password hashes, salts, or passwords. The
credentials file contains salted PBKDF2 hashes, so an existing password cannot
be recovered from it. The handoff CSV is the only plaintext copy produced and
new output paths are required because credential creation refuses to overwrite
files.

Quote local paths that contain spaces. For example, the safe account listing
and the separate, local-only handoff review are:

```bash
python3 -m tools.offline_task_manager list-online-users \
  --credentials "$HOME/Library/Application Support/ScienceVision/online-smoke/credentials.json"

less "$HOME/Library/Application Support/ScienceVision/online-smoke/account-handoff.csv"
# Use cat only in a private local terminal where its output will not be logged:
cat "$HOME/Library/Application Support/ScienceVision/online-smoke/account-handoff.csv"
```

Never paste the handoff output into chat, logs, tickets, or shell commands. Use
`list-online-users` for routine account inspection and open the handoff only
when a password must be delivered to its assigned user.

If the handoff is lost or a credential may be compromised, rotate the whole
set: create credentials, a handoff, and a session key at new private paths;
update the service's `--credentials` and `--session-secret` paths; restart the
service; then distribute the new handoff through an approved private channel.
The new session key invalidates existing sessions. Retire the old files only
after the restarted service and new logins have been verified.

The server has no fixed batch. Administrators can publish a new fully assigned
batch while it is running; each expert sees the resulting active immutable
bundle on the next load. `--batch-id` remains optional as a one-time startup
publish for older automation.

The online server intentionally does not hash a multi-gigabyte asset again for
every HTTP Range request. Keep every active batch's `asset_root` immutable and
read-only to the service account for the full publication lifetime. Publication
performs the full SHA-256 check; requests then enforce the published path
closure and reject traversal and symlink escapes. Replace changed assets under
a new `batch_id` and publish that batch instead of editing an active root.

Caddy must proxy `/sv-annotate` and `/sv-annotate/*` to
`127.0.0.1:4174`. Do not expose port 4174 directly. Experts land at
`/sv-annotate/`; administrators are redirected to `/sv-annotate/admin/`.
The expert signs in, reviews only their active tasks and exact asset closure,
and uses **Submit results**. No package directory is required by the online
path.

## Browser MCP: Functions panel and trajectory capture

The browser MCP layer has two halves that must be version-matched.

The **in-iframe half** ships inside each package's viewer runtime
(`viewer/shared/viewer-mcp-api.js` plus one adapter per renderer). It is the
command boundary: the parent page never touches Mol\* or NiiVue directly.

The **parent-page half** is served by the host, from
`expert_app/mcp/` (see its `PROVENANCE.md` for the exact vendored version and
why that version was chosen). It provides the schema-driven **Functions**
panel and the **Trajectory** capture drawer, and it owns `frame.src` so each
navigation carries a token the panel can match on load.

`build_runtime_manifest` reports both halves together as the
`viewer-mcp-functions` capability, with a SHA-256 record per file. The
capability is additive: bundles published before it exist do not require it and
stay compatible.

Tool policy is derived per task, not configured. `viewer_get_state` and
`viewer_reset` are always available as viewer utilities; a tool named in the
task's `tool_policy.allowed_tools` is `task_allowed_tools`; anything else is a
`free_exploration_extension`, hidden until the expert opts in and receipted as
`policy_compliant: false`.

### Trajectory submission

Drafts live in the browser (IndexedDB `sciencevision-expert-annotations`) until
the expert submits. **Submit** is enabled only when the host page injects both

```text
<meta name="sciencevision-trajectory-submit-endpoint" content="{base}/api/expert/trajectory">
<meta name="sciencevision-csrf-token" content="...">
```

`serve-online` injects both into `/expert` for a signed-in expert, so the
authenticated route exists there and nowhere else. The endpoint requires the
`expert` role, the session cookie, and a matching `X-CSRF-Token`; a supplied
`Idempotency-Key` header must agree with the payload's own key.

Submissions are an append-only artifact stream, deliberately independent of
`results`: a trajectory is evidence, not a grade, so importing one never
touches a review answer. Metadata and the payload digest land in the
`trajectory_submissions` table, keyed by `idempotency_key`; the multi-megabyte
document itself (base64 screenshots) is written to
`{trajectory-root}/{bundle_id}/{submission_id}.json`. The default root is a
`trajectories/` directory beside the database — under systemd it must fall
inside `ReadWritePaths` — and `--trajectory-root` overrides it.

Replaying the same key with the same body is acknowledged with
`replayed: true`. The same key with a *different* body is refused rather than
resolved, as is a key belonging to another expert or a `task_id` outside the
expert's active bundle. The request body is
`schemas/expert_trajectory_submission.schema.json`.

The review result envelope's own `trajectory` block still reads
`sciencevision.trajectory_ref.v1` / `not_collected`; that field describes what
the *result* submission inlines, and inlining a trajectory there would require
a result-contract version bump. Join the two streams on
`(expert_id, task_id)`.

To expose a bundle in the general task viewer, use a same-origin manifest URL:

```text
/viewer/sciencevision_tasks.html?manifest=/path/to/bundle_manifest.json
```

Task and mask paths are resolved against the manifest directory and must remain
inside its declared asset closure. The existing `?set=` workflow remains
unchanged; when both parameters are supplied, a failed manifest load falls back
to the explicit `set`.
