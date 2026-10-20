# ScienceVision Release Pins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `UniPat-AI/ScienceVision` reaches a state where every image digest it references is accounted for, its `v1.0.0` release tag exists on all ten canonical packages, and its dataset pin points at a `review-v11` commit that no longer contains the rejected `mvd-068`.

**Architecture:** Two external writes (a GHCR retag and a Hugging Face deletion) bracket four repository changes. The validator gains a digest-accounting rule first and is observed failing on the current stale references; the resource files and documentation then change until it passes. Nothing in this plan touches the harness code — that is PR 2.

**Tech Stack:** Python 3.12, `jsonschema` 4.23.0 (the only pinned validation dependency), `skopeo` for registry copies, the `hf` CLI for the dataset write.

**Spec:** `docs/superpowers/specs/2026-10-10-sciencevision-release-consolidation-design.md` (§5.2, §5.3, §6 PR 1, §9 D3, Appendix C)

## Global Constraints

- All work happens in a fresh clone of `UniPat-AI/ScienceVision` under `/Users/sue/Research/.worktrees/`, never in a shared checkout. Configure the clone with `user.name "tianyi-UniPat"` and `user.email "286875149+tianyi-UniPat@users.noreply.github.com"` before the first commit.
- Commits, pull requests, code and documentation in this repository are **English only**. No Chinese characters anywhere outside the `harness/` vendored source (which does not exist until PR 2).
- Never write ground truth, internal host paths, private IP addresses, gateway names, personal names, or credentials into this repository.
- `resources/images.json` is the **single source of truth** for image identity. Documentation may quote digests, but the validator is what keeps them honest.
- `docker://` pulls must use digests, not tags, in anything described as reproducible.
- The `v1.0.0` tag is created with `skopeo copy --preserve-digests`, which copies an existing manifest **by digest** rather than rebuilding it. If a copy ever reports a different digest, stop — the tag is wrong and the images must not be rebuilt to make it match.
- Do not add a `latest` tag. Do not delete the three untagged scorer manifests.
- Two steps are outward-facing and each needs explicit confirmation from the owner before it runs: **Task 1** (GHCR tag creation) and **Task 5** (Hugging Face deletion). Tasks 2–4 and 6 are local and reversible.

## Review Focus

The failure modes below are the ones a reasonable person would hit but no task's tests exercise. Each is pinned by a test in the task that owns the code.

- **A digest that is untagged but still the correct pin.** `d07c59a7…` is the scorer that BV2 `b47f13ac` defaults to. After the 10-09 normalization the GHCR *tag* points elsewhere, but the *manifest* still pulls by digest. A validator that demands "every digest has a tag" would reject a correct pin and push someone to re-tag with the wrong manifest. The rule must be "current tag target **or** explicitly retained as superseded".
- **Editing `performance/examples/run.json`.** It is a synthetic example whose `images[].reference` is checked only by pattern, not by membership in the inventory — so a stale digest there passes every existing test silently. It must be caught by the new scan or it will drift forever.
- **A digest that appears in a Markdown link or code fence.** `docs/evaluation.md` and `README.md` both carry bare `ghcr.io/...@sha256:...` strings inside fenced blocks. The scan must read whole files, not only link targets.
- **Recording the `v1.0.0` digest before reading it back.** The expected digest is known in advance only because `--preserve-digests` guarantees it. If the readback is skipped, a failed copy produces an inventory that lies.
- **Re-pinning the dataset without re-pinning `harness.json`.** `validate_references` asserts `harness.dataset_compatibility.dataset_commit == dataset.source.commit`. Changing one and not the other fails CI rather than silently drifting — which is the intent — but an executor who only edits `dataset.json` will see a confusing failure.

---

### Task 1: Create the `v1.0.0` tag on every canonical package

**Files:** none. This task changes registry state only.

**Interfaces:**
- Consumes: nothing.
- Produces: the ten `<package>:v1.0.0` tags that Task 2 records, and the digest readback that Task 2 writes into `resources/images.json`.

> **Confirmation gate.** Ask the owner before running any `skopeo copy` in this task. State exactly what will be created: ten tags, package names unchanged, no rebuild, no deletion, existing tags untouched.

The copy source and the expected digest, one row per package. The expected digest equals the package's **current canonical digest** in `resources/images.json` — the copy must not change it.

| package | copy from tag | expected manifest digest |
|---|---|---|
| `sciencevision-codex` | `server-20260930-h20` | `sha256:43b4bf787e205762368aee65588b7a24bac748f267488769152209632efba04c` |
| `sciencevision-codex-2d` | `server-20260930-h20` | `sha256:ac2043cf244040aefe71e0539dc01d32438061d5937f2c781d84b63451ee20aa` |
| `sciencevision-codex-mcp` | `server-20260930-h20` | `sha256:97ddcbb6bb312176bae8a44f6564f65b6f83fdf381ba6136240bcf10c7e52e8e` |
| `sciencevision-codex-mcp-client` | `release-20260930-h20` | `sha256:b4c03b16847e7f5d02a4c78e61c89e9f5fe896845663e532ee1f6ae2f8ae04fe` |
| `sciencevision-claude-code` | `server-20261002-h20` | `sha256:e7e5ce2ce6393acc05f3165c3313b392e26501e29fff3e6fcd3f2f601f4075a5` |
| `sciencevision-claude-code-2d` | `server-20261002-h20` | `sha256:c385efa0c471923da9a770c830602a0220d6fa72cbba1278879762a4633b358f` |
| `sciencevision-claude-code-mcp` | `server-20261002-h20` | `sha256:ac9722518747ac0aae4ff52f8a9dacea5f03c23614d507903db83db98732cd59` |
| `sciencevision-claude-code-mcp-client` | `release-20261002-h20` | `sha256:597cc446d579d0ccca961f0eed5b133d7925337fd50008594632117a8fec1b2a` |
| `sciencevision-mcp-worker` | `server-20260924-h20` | `sha256:292e3770e3a7ffd3397f575b9282bd3d65db2a6e3a30ceae838666949a56eafa` |
| `sciencevision-scorer` | `server-20261005-h20` | `sha256:b97ca59f66fb66838a9a5a5fcf7b51230df0ce0e21664523c52f1e3f02d4f1ee` |

Not tagged: `sciencevision-scorer:server-20260930-h20` and `:server-20260928b-h20` (historical pins, kept for existing receipts), `sciencevision-claude-code-mcp:mcpfix-20261006-h20` (oracle patch), and the two `-agent` legacy aliases.

Write the copy driver to a scratch directory outside the repository. It must be idempotent: re-running it after a successful run copies the same digest onto the same tag and changes nothing.

- [ ] **Step 1: Write the retag and readback script**

Create `~/sv-pins/retag.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
REG=ghcr.io/unipat-ai
pairs=(
  "sciencevision-codex:server-20260930-h20:43b4bf787e205762368aee65588b7a24bac748f267488769152209632efba04c"
  "sciencevision-codex-2d:server-20260930-h20:ac2043cf244040aefe71e0539dc01d32438061d5937f2c781d84b63451ee20aa"
  "sciencevision-codex-mcp:server-20260930-h20:97ddcbb6bb312176bae8a44f6564f65b6f83fdf381ba6136240bcf10c7e52e8e"
  "sciencevision-codex-mcp-client:release-20260930-h20:b4c03b16847e7f5d02a4c78e61c89e9f5fe896845663e532ee1f6ae2f8ae04fe"
  "sciencevision-claude-code:server-20261002-h20:e7e5ce2ce6393acc05f3165c3313b392e26501e29fff3e6fcd3f2f601f4075a5"
  "sciencevision-claude-code-2d:server-20261002-h20:c385efa0c471923da9a770c830602a0220d6fa72cbba1278879762a4633b358f"
  "sciencevision-claude-code-mcp:server-20261002-h20:ac9722518747ac0aae4ff52f8a9dacea5f03c23614d507903db83db98732cd59"
  "sciencevision-claude-code-mcp-client:release-20261002-h20:597cc446d579d0ccca961f0eed5b133d7925337fd50008594632117a8fec1b2a"
  "sciencevision-mcp-worker:server-20260924-h20:292e3770e3a7ffd3397f575b9282bd3d65db2a6e3a30ceae838666949a56eafa"
  "sciencevision-scorer:server-20261005-h20:b97ca59f66fb66838a9a5a5fcf7b51230df0ce0e21664523c52f1e3f02d4f1ee"
)
for row in "${pairs[@]}"; do
  IFS=: read -r pkg srctag want <<<"$row"
  skopeo copy --preserve-digests --all \
    "docker://$REG/$pkg:$srctag" "docker://$REG/$pkg:v1.0.0" >/dev/null
  got=$(skopeo inspect --raw "docker://$REG/$pkg:v1.0.0" | sha256sum | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then
    echo "FAIL $pkg v1.0.0=$got want=$want" >&2
    exit 1
  fi
  echo "OK $pkg sha256:$got"
done
```

`skopeo inspect --raw` emits the manifest bytes, so hashing them yields the manifest digest directly — that is the same value `images.json` records in `canonical_manifest_digest`.

- [ ] **Step 2: Confirm with the owner, then run it**

Run: `bash ~/sv-pins/retag.sh`

Expected: ten `OK` lines, each digest equal to the table above. Any `FAIL` line means the copy produced a different manifest; stop and report rather than tagging further packages.

- [ ] **Step 3: Capture the readback for the commit message**

Run:

```bash
bash ~/sv-pins/retag.sh > ~/sv-pins/retag-readback.txt 2>&1
cat ~/sv-pins/retag-readback.txt
```

The second run is a no-op copy and proves idempotence, which is also the evidence that no bytes changed.

- [ ] **Step 4: No commit**

Registry state is not repository state. Nothing is committed in this task.

---

### Task 2: Teach the validator to account for every digest

**Files:**
- Modify: `scripts/validate_repository.py` (`validate_references`, currently lines 131–152)
- Test: `tests/test_images_inventory.py` (create)

**Interfaces:**
- Consumes: `resources/images.json` in its current shape.
- Produces: `validate_release_tags(images)` and `iter_referenced_digests()` — `validate_release_tags(images: dict) -> None` raises `ValueError` on a bad inventory; `iter_referenced_digests() -> list[tuple[str, str]]` returns `(relative_path, digest)` for every `sha256:` string that follows a `ghcr.io/unipat-ai/` reference in any tracked text file.

The rule: a digest referenced anywhere in the repository must be either **the current canonical digest of an inventory entry**, or **listed in some entry's `superseded_manifest_digests`**. The second branch is what keeps `d07c59a7…` legitimate — it is untagged but still the pin that BV2 `b47f13ac` pulls by digest, and it must not be deleted.

Create the `tests/` directory and a `pytest` runner for it. Add `pytest` to `requirements-validation.txt` alongside `jsonschema==4.23.0`, and run the tests in CI before the repository validator.

- [ ] **Step 1: Write the failing test**

Create `tests/test_images_inventory.py`:

```python
"""The inventory must account for every digest the repository references."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_repository as vr  # noqa: E402


def inventory() -> dict:
    return json.loads((ROOT / "resources/images.json").read_text())


def current_digests(images: dict) -> set[str]:
    return {item["canonical_manifest_digest"] for item in images["images"]}


def superseded_digests(images: dict) -> set[str]:
    out: set[str] = set()
    for item in images["images"]:
        out.update(item.get("superseded_manifest_digests", []))
    return out


def test_every_referenced_digest_is_current_or_retained():
    images = inventory()
    known = current_digests(images) | superseded_digests(images)
    unknown = sorted({d for _, d in vr.iter_referenced_digests()} - known)
    assert unknown == [], f"unaccounted image digests: {unknown}"


def test_superseded_digests_are_not_also_current():
    images = inventory()
    overlap = current_digests(images) & superseded_digests(images)
    assert overlap == set(), f"a digest cannot be both current and superseded: {overlap}"


def test_the_v1_release_tag_covers_the_canonical_packages():
    images = inventory()
    tagged = {item["canonical"].rsplit(":", 1)[0]
              for item in images["images"] if "v1.0.0" in item.get("release_tags", [])}
    assert images["release"]["tag"] == "v1.0.0"
    assert len(tagged) == 10, f"expected ten packaged v1.0.0 tags, got {sorted(tagged)}"
    assert "ghcr.io/unipat-ai/sciencevision-scorer" in tagged
    for role in ("historical scorer", "pinned harness scorer", "experimental oracle MCP Claude Code"):
        item = next(x for x in images["images"] if x["role"] == role)
        assert item.get("release_tags", []) == [], f"{role} must not carry v1.0.0"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_images_inventory.py -v`

Expected: FAIL — `AttributeError: module 'validate_repository' has no attribute 'iter_referenced_digests'`.

- [ ] **Step 3: Add the scan and the release checks**

In `scripts/validate_repository.py`, add above `validate_references`:

```python
_SCANNED_SUFFIXES = {".md", ".json", ".jsonl", ".yml", ".yaml", ".py", ".txt", ".toml"}
_GHCR_DIGEST = re.compile(r"ghcr\.io/unipat-ai/[A-Za-z0-9._/-]+@(sha256:[0-9a-f]{64})")
_SKIPPED_DIRS = {".git", ".venv", "__pycache__", "data", "work", "eval-baseline"}


def iter_referenced_digests() -> list[tuple[str, str]]:
    """Every GHCR digest the repository mentions, with the file that mentions it."""
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        if any(part in _SKIPPED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for digest in _GHCR_DIGEST.findall(text):
            found.append((str(path.relative_to(ROOT)), digest))
    return found


def validate_release_tags(images: dict) -> None:
    require(isinstance(images.get("release"), dict), "images.json needs a release block")
    require(images["release"]["tag"] == "v1.0.0", "release tag must be v1.0.0")
    require(images["release"]["digest_preserved"] is True,
            "the release tag must be a preserve-digests copy")
    current, superseded = set(), set()
    for item in images["images"]:
        require(isinstance(item.get("release_tags", []), list),
                f"release_tags must be a list: {item['role']}")
        digest = item["canonical_manifest_digest"]
        require(digest not in current, f"duplicate canonical digest: {item['role']}")
        current.add(digest)
        for old in item.get("superseded_manifest_digests", []):
            require(re.fullmatch(r"sha256:[0-9a-f]{64}", old) is not None,
                    f"malformed superseded digest: {item['role']}")
            require(old not in superseded, f"duplicate superseded digest: {old}")
            superseded.add(old)
    require(not (current & superseded), "a digest cannot be both current and superseded")
    known = current | superseded
    unknown = sorted({d for _, d in iter_referenced_digests()} - known)
    require(not unknown, f"unaccounted image digests: {unknown}")
    counted = len(iter_referenced_digests())
    require(counted > 0, "the digest scan found nothing, which means it is broken")
```

Then call it from `validate_references`, immediately after the `images = read_json(...)` line and before the existing per-image loop:

```python
    validate_release_tags(images)
```

And in `main`, add the count to the printed summary so a silently-empty scan is visible:

```python
    print(json.dumps({"status": "pass", "schemas": 3, "legacy_index_rows_checked": count,
                      "image_digests_accounted": len(iter_referenced_digests()),
                      "scope": "local references, links, schema and numeric consistency; no model or container execution"}))
```

- [ ] **Step 4: Run the test and observe the real failures**

Run: `python -m pytest tests/test_images_inventory.py -v`

Expected: two FAIL, one PASS.

- `test_superseded_digests_are_not_also_current` PASSES — it reads through `.get(..., [])`, so an inventory with no superseded entries trivially has no overlap.
- `test_the_v1_release_tag_covers_the_canonical_packages` FAILS with `KeyError: 'release'`.
- `test_every_referenced_digest_is_current_or_retained` FAILS with `unaccounted image digests: ['sha256:751eedbd…', 'sha256:85607538…', 'sha256:d07c59a7…']`.

Those three digests **are** the deliverable of the next two tasks. Record the exact list in the commit message.

- [ ] **Step 5: Commit**

```bash
cd /Users/sue/Research/.worktrees/ScienceVision
git add scripts/validate_repository.py tests/test_images_inventory.py requirements-validation.txt
git commit -m "$(cat <<'EOF'
test(validation): require every referenced image digest to be accounted for

A digest may be the current canonical digest of an inventory entry, or an
entry's explicitly retained superseded digest, but it may not be
unaccounted for. The retained branch matters: the scorer BV2 b47f13ac
pins by digest is no longer a tag target, yet it is still the correct pin
and must not be deleted.

The scan reads whole files rather than link targets, because the stale
digests sit inside fenced blocks in the README and the evaluation guide.
EOF
)"
```

---

### Task 3: Normalize the scorer digests and record the release tag

**Files:**
- Modify: `resources/images.json`
- Test: `tests/test_images_inventory.py` (the failures from Task 2 must clear)

**Interfaces:**
- Consumes: the readback from Task 1.
- Produces: `images["release"]`, per-entry `release_tags`, and per-entry `superseded_manifest_digests`, all of which `validate_release_tags` requires.

The three scorer entries carry manifest digests that the 2026-10-09 skopeo normalization made untagged. Each GHCR scorer tag now points at the manifest that Docker Hub already served, and that manifest's digest is already recorded in the entry as `source_manifest_digest`. The fix moves the old value into `superseded_manifest_digests` and promotes the source value to `canonical_manifest_digest`.

`config_digest` does **not** change: equal config digests are exactly what established that the two manifests declare the same rootfs and runtime configuration.

- [ ] **Step 1: Apply the three digest moves**

Edit `resources/images.json`:

For the entry with `"role": "historical scorer"` (currently line 166):

```json
      "canonical_manifest_digest": "sha256:7e5471e1149ad8d84f15b4eab98d8d34d0070ee5fec15b470d2f001be6b9aea1",
      "canonical_digest_reference": "ghcr.io/unipat-ai/sciencevision-scorer@sha256:7e5471e1149ad8d84f15b4eab98d8d34d0070ee5fec15b470d2f001be6b9aea1",
      "superseded_manifest_digests": [
        "sha256:85607538c3f268200f8c1ffd2b1c4b27805df2f7c31b8fdc04eb478e4813d498"
      ],
```

For `"role": "pinned harness scorer"`:

```json
      "canonical_manifest_digest": "sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3",
      "canonical_digest_reference": "ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3",
      "superseded_manifest_digests": [
        "sha256:d07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062"
      ],
```

For `"role": "platform package scorer"`:

```json
      "canonical_manifest_digest": "sha256:b97ca59f66fb66838a9a5a5fcf7b51230df0ce0e21664523c52f1e3f02d4f1ee",
      "canonical_digest_reference": "ghcr.io/unipat-ai/sciencevision-scorer@sha256:b97ca59f66fb66838a9a5a5fcf7b51230df0ce0e21664523c52f1e3f02d4f1ee",
      "superseded_manifest_digests": [
        "sha256:751eedbd84f4997b897366008e0133c0b7402f65c47e5f531ad2db0a499459d8"
      ],
```

Keep each entry's `usage` string unchanged — "Historical release and task-contract reproduction; retain for existing receipts", "Default of the pinned eval-baseline harness; also used by oracle validation", and "Later platform-package scorer; not a drop-in replacement for historical scoring" all still describe the right thing.

- [ ] **Step 2: Add the release block and the per-entry tags**

Add to the top level of `resources/images.json`, after `"verification"`:

```json
  "release": {
    "tag": "v1.0.0",
    "created_at": "2026-10-13",
    "method": "skopeo copy --preserve-digests --all, within the same GHCR repository",
    "digest_preserved": true,
    "note": "v1.0.0 is a second tag on an existing manifest, not a rebuild. The manifest digest and the layer bytes are identical to the source tag listed on the same entry."
  },
```

Add `"release_tags": ["v1.0.0"],` to these ten entries, inserted directly after `"role"`: `raw-file Codex`, `rendered-view Codex`, `bundled MCP Codex`, `MCP client Codex`, `raw-file Claude Code`, `rendered-view Claude Code`, `bundled MCP Claude Code`, `MCP client Claude Code`, `external MCP worker`, `platform package scorer`.

Add `"release_tags": [],` to the three that must not carry it: `historical scorer`, `pinned harness scorer`, `experimental oracle MCP Claude Code`.

Update `"verified_at"` to `"2026-10-13T00:00:00+00:00"` and set `"status"` to `"v1_release_tagged_canonical_packages_verified"`.

- [ ] **Step 3: Run the tests**

Run: `python -m pytest tests/test_images_inventory.py -v`

Expected: `test_superseded_digests_are_not_also_current` and `test_the_v1_release_tag_covers_the_canonical_packages` PASS. `test_every_referenced_digest_is_current_or_retained` still FAILS, now listing only the two documentation sites rather than the inventory — the inventory's own references are accounted for. Confirm the remaining unaccounted digests are exactly the ones Task 4 fixes:

```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
import validate_repository as vr
for where, digest in vr.iter_referenced_digests():
    print(where, digest)
"
```

- [ ] **Step 4: Commit**

```bash
git add resources/images.json
git commit -m "$(cat <<'EOF'
fix(resources): normalize the scorer digests and record v1.0.0

The 2026-10-09 registry normalization left three scorer manifest digests
untagged, because each GHCR scorer tag now points at the manifest Docker
Hub already served. Promote that manifest to canonical and retain the old
digest explicitly, so a reader can tell a deliberately retained pin from
a broken reference.

The config digests are unchanged, which is the same evidence the original
comparison used: equal config digests mean equal declared rootfs and
runtime configuration.

v1.0.0 is recorded per entry as a second tag on an existing manifest.
EOF
)"
```

---

### Task 4: Update every image-digest reference in prose

**Files:**
- Modify: `README.md` (line 95)
- Modify: `docs/evaluation.md` (line 22)
- Modify: `docs/containers.md`
- Modify: `performance/examples/run.json` (line 40)

**Interfaces:**
- Consumes: the digests recorded in Task 3.
- Produces: a repository where `python scripts/validate_repository.py` reports `image_digests_accounted` with no unaccounted digests. The dataset revision references are **not** touched here — Task 5 owns them.

`performance/examples/run.json` is the one that no existing check covers: `images[].reference` is validated only against a regex, never against the inventory, so a stale digest there would drift forever. It must be updated by hand.

- [ ] **Step 1: Replace the two stale digests in the README and the evaluation guide**

In `README.md`, line 95, and `docs/evaluation.md`, line 22, replace:
`ghcr.io/unipat-ai/sciencevision-scorer@sha256:d07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062`
with:
`ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3`

The pin is the same scorer release; only the manifest that the tag resolves to changed. Both files describe it as the pinned harness default, which is still true.

In `performance/examples/run.json`, line 40, replace:
`ghcr.io/unipat-ai/sciencevision-scorer@sha256:751eedbd84f4997b897366008e0133c0b7402f65c47e5f531ad2db0a499459d8`
with:
`ghcr.io/unipat-ai/sciencevision-scorer@sha256:b97ca59f66fb66838a9a5a5fcf7b51230df0ce0e21664523c52f1e3f02d4f1ee`

The example stands in for a platform-package run, so the platform-package scorer is the correct stand-in.

- [ ] **Step 2: Update the container guide's release-tag table**

Read `docs/containers.md` and add a `v1.0.0` column to whatever release-tag table it carries, stating for each package the source tag from Task 1's table. Add one sentence: `v1.0.0 is a preserve-digests copy of the listed source tag inside the same GHCR repository, so the manifest digest is unchanged; the older tags remain valid aliases.` Do not remove or alter the existing tag rows.

- [ ] **Step 3: Run the full validator**

Run:

```bash
python -m pip install -r requirements-validation.txt
python -m pytest tests/ -q
python scripts/validate_repository.py
```

Expected: tests pass; the validator prints `"status": "pass"` with `"image_digests_accounted"` greater than zero and no `unaccounted image digests` error.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/containers.md docs/evaluation.md performance/examples/run.json
git commit -m "$(cat <<'EOF'
docs: reference the current scorer digests

Three sites still named the manifest the scorer tag resolved to before
the 2026-10-09 normalization. The performance example mattered most: its
image reference is checked only against a pattern, so it would have
drifted unnoticed.

The v1.0.0 rows are added alongside the existing tags rather than
replacing them, because the older tags remain valid.
EOF
)"
```

---

### Task 5: Remove `mvd-068` from `review-v11` and re-pin the dataset

**Files:**
- Modify: `resources/dataset.json`
- Modify: `resources/harness.json` (`dataset_compatibility.dataset_commit`)
- Modify: `README.md` (lines 15, 36, 50), `docs/dataset.md` (lines 5, 6, 19, 29)
- Modify: `performance/examples/run.json` (`dataset.revision_name`, `dataset.commit`, `dataset.index_sha256`)

**Interfaces:**
- Consumes: the HF dataset `elorian-ai/ScienceVision` on branch `review-v11`.
- Produces: a new 40-hex commit and index hash that Task 6 reports and that any later plan must treat as the dataset pin.

> **Confirmation gate.** Ask the owner before deleting. State exactly what will be removed: the `mvd-068` task directory on `review-v11` only, 73 files, from a task that the owner rejected. `main` does not contain it and is not touched. The deletion is a new commit on `review-v11`; the previous commit stays reachable and the deletion can be reverted by another commit.

- [ ] **Step 1: Measure the current pin before changing anything**

Run and record all three numbers — do not reuse the values currently in `resources/dataset.json`, because they were measured at a different revision:

```bash
mkdir -p ~/sv-pins && cd ~/sv-pins
python - <<'PY'
from huggingface_hub import HfApi
api = HfApi()
repo = "elorian-ai/ScienceVision"
for rev in ("0f9e91ed597a49c205960a0f96ff3d4457db682e", "review-v11"):
    info = api.repo_info(repo, repo_type="dataset", revision=rev)
    total = count = 0
    for entry in api.list_repo_tree(repo, repo_type="dataset", revision=rev, recursive=True):
        if entry.__class__.__name__ == "RepoFile":
            total += entry.size or 0
            count += 1
    print(rev, info.sha, count, total)
PY
```

`list_repo_tree` carries sizes; `list_repo_files` does not. `HfApi()` picks up the token already stored at `~/.cache/huggingface/token`.

Expected: two lines, each `<rev> <40-hex sha> <file_count> <logical_file_bytes>`. The `0f9e91ed` line must reproduce `file_count` 21653 and `logical_file_bytes` 8224311200. **If it does not, stop** — the counting method differs from the one that produced the recorded values, and re-pinning with a different method would silently redefine what those two fields mean.

- [ ] **Step 2: Confirm with the owner, then delete**

```bash
python - <<'PY'
from huggingface_hub import HfApi
api = HfApi()
api.delete_folder(
    path_in_repo="mvd-068",
    repo_id="elorian-ai/ScienceVision",
    repo_type="dataset",
    revision="review-v11",
)
PY
```

The path must be the task directory as it appears in `index.jsonl`. If the index names it as `<domain>/mvd-068`, pass that full path instead, and verify with:

```bash
hf download elorian-ai/ScienceVision index.jsonl --type dataset \
  --revision review-v11 --local-dir ~/sv-pins/after
grep -c mvd-068 ~/sv-pins/after/index.jsonl || echo "mvd-068 gone"
```

- [ ] **Step 3: Read back the new commit and re-measure**

Run Step 1 again with `review-v11` only. Record `<new_commit>`, the new `file_count`, the new `logical_file_bytes`, and the new `index.jsonl` hash:

```bash
hf download elorian-ai/ScienceVision index.jsonl --type dataset \
  --revision "$NEW_COMMIT" --local-dir ~/sv-pins/after
shasum -a 256 ~/sv-pins/after/index.jsonl
```

Expected: `index_sha256` differs from the old `d90b7213…`, and `file_count` equals the old count minus the number of files under `mvd-068`. Confirm that arithmetic explicitly; if it does not hold, something else changed on the branch and the pin must not be moved without understanding what.

- [ ] **Step 4: Write the new pin into both resource files**

```bash
cd /Users/sue/Research/.worktrees/ScienceVision
python - "$NEW_COMMIT" "$NEW_COUNT" "$NEW_BYTES" "$NEW_INDEX_SHA" <<'PY'
import json, pathlib, sys
commit, count, nbytes, index_sha = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
p = pathlib.Path("resources/dataset.json")
d = json.loads(p.read_text())
d["verified_at"] = "2026-10-13"
d["source"]["url"] = f"https://huggingface.co/datasets/elorian-ai/ScienceVision/tree/{commit}"
d["source"]["revision_name"] = commit
d["source"]["commit"] = commit
d["file_count"] = count
d["logical_file_bytes"] = nbytes
d["index_sha256"] = index_sha
p.write_text(json.dumps(d, indent=2) + "\n")

h = pathlib.Path("resources/harness.json")
j = json.loads(h.read_text())
j["verified_at"] = "2026-10-13"
j["dataset_compatibility"]["dataset_commit"] = commit
h.write_text(json.dumps(j, indent=2) + "\n")
print("dataset + harness updated to", commit)
PY
git diff --stat resources/
```

The two files must move together: `validate_references` asserts `harness["dataset_compatibility"]["dataset_commit"] == dataset["source"]["commit"]`. An executor who edits only `dataset.json` will see that assertion fail, which is the intended behaviour.

- [ ] **Step 5: Verify the index hash against a real download**

Run:

```bash
python scripts/check_dataset.py ~/sv-pins/after --index-only
```

Expected: `"status": "pass"` with `task_count` 250 and the new `index_sha256`.

- [ ] **Step 6: Sweep the revision through the prose**

The old revision appears in four prose sites and one example record. All of them must move, or the repository will tell a reader to download a revision the inventory no longer pins.

```bash
cd /Users/sue/Research/.worktrees/ScienceVision
OLD=0f9e91ed597a49c205960a0f96ff3d4457db682e
NEW=$(python -c "import json;print(json.load(open('resources/dataset.json'))['source']['commit'])")
NEW_SHA=$(python -c "import json;print(json.load(open('resources/dataset.json'))['index_sha256'])")
OLD_SHA=$(python -c "import json;print(json.load(open('performance/examples/run.json'))['dataset']['index_sha256'])")

grep -rl "$OLD" README.md docs performance || true
grep -rl "$OLD" README.md docs performance | xargs sed -i.bak "s/$OLD/$NEW/g"
sed -i.bak "s/$OLD_SHA/$NEW_SHA/g" performance/examples/run.json
find . -name '*.bak' -delete
grep -rn "$OLD\|$OLD_SHA" README.md docs performance resources && exit 1
echo "no stale revision references"
```

Expected: the first `grep -rl` lists `README.md`, `docs/dataset.md` and `performance/examples/run.json`; the last lines print `no stale revision references`. `performance/examples/run.json` carries both the revision (lines 9–10) and the index hash (line 11), and it is the reason this sweep is not just a text substitution.

- [ ] **Step 7: Run everything**

```bash
python -m pytest tests/ -q && python scripts/validate_repository.py
python scripts/check_dataset.py ~/sv-pins/after --index-only
```

Expected: all pass, with no unaccounted image digests and no stale revision references.

- [ ] **Step 8: Commit**

```bash
git add resources/dataset.json resources/harness.json README.md docs/dataset.md \
        performance/examples/run.json
git commit -m "$(cat <<'EOF'
chore(resources): re-pin the dataset after removing mvd-068

The rejected task was still present on review-v11 and absent from main,
so the pinned revision carried a task the dataset index does not list.
Removing it changes the revision, the file count, the logical byte total
and the index hash, all of which are recorded here.

Every reader-facing reference moves in the same commit, including the
performance example's revision and index hash — that record is checked
only against a pattern, so it would otherwise keep pointing at a revision
the inventory no longer pins.
EOF
)"
```

---

### Task 6: Bring `harness.json` and `CONTRIBUTING.md` in line

**Files:**
- Modify: `resources/harness.json`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: Task 5's new commit, Task 3's release block.
- Produces: a `harness.json` that names the scorer digest the pinned harness actually passes, and a `CONTRIBUTING.md` that no longer tells contributors to change the implementation in another repository. `harness.json`'s `entrypoints` are unchanged — PR 2 keeps every module name, so those paths stay correct.

`CONTRIBUTING.md` currently says "Evaluation implementation changes belong in the linked BabyVision-v2 repository." After PR 2 that is false. This task only changes the sentence and adds the validation command; the substantive Evaluation section rewrite is PR 2's.

- [ ] **Step 1: Record the pinned scorer as a superseded-aware reference**

In `resources/harness.json`, add after `"commit"`:

```json
  "default_scorer_manifest_digest": "sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3",
  "default_scorer_note": "The digest the pinned harness passes to SCI3D_SCORER_IMAGE. It equals resources/images.json entry 'pinned harness scorer', whose superseded_manifest_digests retains the manifest this tag resolved to before the 2026-10-09 normalization.",
```

- [ ] **Step 2: Correct the contributing guidance**

In `CONTRIBUTING.md`, replace:

```
Open an issue or pull request in this repository for documentation, resource references, or performance-format changes. Evaluation implementation changes belong in the linked BabyVision-v2 repository.
```

with:

```
Open an issue or pull request in this repository for documentation, resource references, performance-format changes, and evaluation implementation changes.
```

and replace:

```
Run `python scripts/validate_repository.py` with the validation requirements installed.
```

with:

```
Run `python scripts/validate_repository.py` and `python -m pytest tests/ -q` with the validation requirements installed.
```

- [ ] **Step 3: Run everything**

Run:

```bash
python -m pytest tests/ -q && python scripts/validate_repository.py
```

Expected: `"status": "pass"`, `"image_digests_accounted"` greater than zero, no unaccounted digests.

- [ ] **Step 4: Commit**

```bash
git add resources/harness.json CONTRIBUTING.md
git commit -m "$(cat <<'EOF'
docs(contributing): keep evaluation changes in this repository

Evaluation implementation is moving into this repository, so the
contributing guide must stop directing it elsewhere. The resource
reference also gains the scorer digest the pinned harness actually
passes, together with a pointer to the superseded digest it replaced.
EOF
)"
```

---

## After the plan

Two follow-ups are deliberately **not** tasks here:

- **Visibility.** Ten GHCR packages are private, and there is no API to change that. Only an organization owner can, in the package settings UI. Nothing in this plan depends on it.
- **The superseded digests are still pullable and must stay that way.** If GHCR ever garbage-collects untagged manifests, the BV2 `b47f13ac` pin breaks. Note it in the pull request body rather than adding a check this repository cannot perform.
