# Docker image release SOP

## Scope and invariants

The Docker Hub namespace is suermars. Release image names follow this pattern:

~~~text
suermars/sci3d-mcp-worker:<release-tag>
suermars/sci3d-codex-mcp:<release-tag>
suermars/sci3d-claude-code-mcp:<release-tag>
suermars/sci3d-scorer:<release-tag>
~~~

Use a date-and-build tag such as server-YYYYMMDD-h20. Never publish or move
latest as part of this workflow. A defective release is remediated by
publishing a new versioned tag and changing the consumer's explicit image
reference, not by retagging an existing release.

Do not print, copy, commit, or log Docker credential files, tokens, passwords,
or credential-helper output. A successful docker push and its returned
repository digest are enough to establish authentication for the release
record.

## 1. Stage auditable source

1. Inspect the source worktree and preserve other contributors' changes.
2. Make a new, uniquely named release staging directory. Do not overwrite an
   existing repository, run, or release directory.
3. Transfer only the required build inputs. Exclude .git, .cache,
   __pycache__, generated builds, and unrelated/raw dataset trees.
4. The worker exception is deliberate: include only viewer/assets.json and
   datasets/_matched/, not their parent directories.
5. Create a compressed input archive, record its byte count and SHA-256, and
   verify both values after transfer before extraction.
6. Preserve the archive and staging directory for audit. Record the original
   release source path and source revision separately when .git is excluded.

For a scorer release, generate its context from the source worktree with
scorer_bundle/build_context.py. Its generated scorer_manifest.json is the
authority for the scorer manifest SHA; do not regenerate it from a synthetic
staging Git repository.

## 2. Build versioned candidates

On the build host, use explicit tags. Do not use latest as an output tag.

~~~bash
bash scripts/harbor/templates/build_sciencevision_mcp_worker_image.sh \
  suermars/sci3d-mcp-worker:server-YYYYMMDD-h20

bash scripts/harbor/templates/build_codex_mcp_image.sh \
  suermars/sci3d-codex-mcp:server-YYYYMMDD-h20

bash scripts/harbor/templates/build_claude_code_mcp_image.sh \
  suermars/sci3d-claude-code-mcp:server-YYYYMMDD-h20

bash scorer_bundle/build_image.sh \
  suermars/sci3d-scorer:server-YYYYMMDD-h20
~~~

If source Git metadata was intentionally excluded from the build host,
preserve the original revision and dirty-input provenance in the release
manifest. Do not claim that a synthetic staging commit is the source commit.

The wrapper builders may inherit a locally cached base image. Before a
production release, record the base image reference/digest in the release
receipt and prefer a pinned base reference whenever the builder supports one.

## 3. Test before publication

Use the project Python 3.12 environment for shell-runtime tests; H20's system
python3 may be too old. At minimum run:

~~~bash
python3 -m pytest -q \
  tests/harbor_adapters/test_mcp_worker_packaging.py \
  tests/harbor_adapters/test_agent_env_table.py \
  tests/harbor_adapters/test_claude_code_mcp_tasks.py \
  tests/harbor_adapters/test_codex_runtime.py \
  tests/scorer_bundle/test_scorer_bundle.py
~~~

Then perform runtime checks appropriate to the release:

- Worker: run as its final non-root user; assert the private
  viewer/assets.json and datasets/_matched/scanqa.json exist; exercise
  sv_get_asset and sv_search_matched_qa; confirm full viewer and datasets
  trees are absent.
- Wrappers: assert absence of sci3d_agent, sci3d_bench, harbor_adapters,
  viewer, datasets, and sciencevision-mcp in the model-facing filesystem;
  assert SV_MCP_HTTP_SURFACE is configured.
- Connectivity: start a healthy worker, share its network namespace with each
  agent wrapper, and issue an MCP initialize HTTP request to
  SV_MCP_HTTP_SURFACE. Require HTTP 200.
- Scorer: run with --network none, as UID 10001, verify it carries no agent,
  MCP, viewer, or dataset source, and compare the image's scorer-manifest
  label with the authoritative generated manifest.

## 4. Push and record digests

Push only after all relevant builds and tests pass:

~~~bash
docker push suermars/sci3d-mcp-worker:server-YYYYMMDD-h20
docker push suermars/sci3d-codex-mcp:server-YYYYMMDD-h20
docker push suermars/sci3d-claude-code-mcp:server-YYYYMMDD-h20
docker push suermars/sci3d-scorer:server-YYYYMMDD-h20
~~~

For each image, record both the local image ID and the repository digest
printed by docker push (or available from docker image inspect). Verify the
exact versioned tag can be resolved from the registry. Do not infer a digest
from a local unpushed image.

## 5. Rollback

1. Stop scheduling or deploying the bad versioned tag.
2. Repoint consumers to the last verified explicit digest or versioned tag.
3. Preserve the failed image, logs, staging directory, and receipts for
   investigation; do not erase evidence as a rollback step.
4. Build and publish a corrective versioned tag after tests pass. Do not
   overwrite the historical tag or use latest as an emergency switch.
5. Update the release manifest and consumer configuration with the
   replacement digest and the reason for rollback.

