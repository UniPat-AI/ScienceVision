# ScienceVision eval images: registries and usage

## Current scorer default (2026-10-06)

The containerized task builder now defaults to the UniPat-AI GHCR copy of
`server-20260930-h20`, pinned by its GHCR manifest digest:

```bash
docker login ghcr.io
docker pull ghcr.io/unipat-ai/sciencevision-scorer@sha256:d07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062
```

The package is private. Check package access before launching an evaluation.
This is a registry migration of the existing default, not an upgrade to the
`server-20261005-h20` scorer: the previous Docker Hub copy and this GHCR copy
have the same configuration digest. Their registry manifest digests differ.
`SCI3D_SCORER_IMAGE` still selects an explicit alternative release for a
delivered task package. Keep the observed image digest in the run receipt.

The local agent/worker build aliases and the image-build scripts are unchanged.
An image-build output requires a tag; it cannot use a pull-by-digest reference
as `docker build -t`. The dated inventory below describes the 2026-10-02 state;
see the [ScienceVision container inventory](https://github.com/UniPat-AI/ScienceVision/blob/main/docs/containers.md)
for current package names, roles, and visibility.

**These three lines are coordinates, not background.**

- **What you are doing**: running a harness evaluation, and you need to pick the
  right image and wire its tag into a task.
- **How you know you got it right**: after `docker pull`, the digest from
  `docker image inspect --format '{{index .RepoDigests 0}}'` matches §2; inside
  the container, `codex --version` / `claude --version` report the version in
  that table.
- **Which registry**: `ghcr.io/unipat-ai/sciencevision-*` is canonical. The
  Docker Hub copies under `suermars/` are still published — the move is a
  dual-push, nothing was retired — but they are the transition path, not the
  target.
- **Out of scope here**: how images are built, published, and where the trust
  boundaries fall. That lives in the release repository under
  `docs/docker-image-release/` (`SOP.md`, `FILE_LAYOUT.md`,
  `IMAGE_INVENTORY.md`).

This page describes the image set as of 2026-10-02.

---

## 1. Tell the two lines apart first, or §2's repeated names will mislead you

The same tag name is a **different image** on each line, because the two lines
build from different repositories:

| | run line | release line |
| --- | --- | --- |
| Source repo | `Science-Vision` | `eval-baseline-release` |
| Where the MCP server lives | **baked into the agent image** (the three `-mcp` images) | a separate `sci3d-mcp-worker` service |
| What the agent image is | full agent + MCP | agent-only wrapper carrying just the CLI |

**The one that bites**: `sci3d-codex-mcp:latest` and
`sci3d-claude-code-mcp:latest` are each claimed by both lines — on the run line
the name is the bundled-MCP image, on the release harness it is the agent-only
wrapper. **This ambiguity is not resolved.** Both the 2026-09-30 and 2026-10-02
rounds left the alias on the run-line image and gave the wrapper a separate
`release-` tag. Confirm which one you want before you run.

---

## 2. Image inventory

Canonical registry: `ghcr.io/unipat-ai`. Package pages live at
`https://github.com/orgs/UniPat-AI/packages/container/package/<name>`.

| Role | GHCR address (use this with `docker pull`) | Digest on GHCR | Intended visibility |
| --- | --- | --- | --- |
| Codex, bare CLI | `ghcr.io/unipat-ai/sciencevision-codex:server-20260930-h20` | `sha256:43b4bf787e205762368aee65588b7a24bac748f267488769152209632efba04c` | public |
| Codex 2D (pre-rendered views) | `ghcr.io/unipat-ai/sciencevision-codex-2d:server-20260930-h20` | `sha256:ac2043cf244040aefe71e0539dc01d32438061d5937f2c781d84b63451ee20aa` | public |
| Claude Code, bare CLI | `ghcr.io/unipat-ai/sciencevision-claude-code:server-20261002-h20` | `sha256:e7e5ce2ce6393acc05f3165c3313b392e26501e29fff3e6fcd3f2f601f4075a5` | public |
| Claude Code 2D | `ghcr.io/unipat-ai/sciencevision-claude-code-2d:server-20261002-h20` | `sha256:c385efa0c471923da9a770c830602a0220d6fa72cbba1278879762a4633b358f` | public |
| Codex agent-only wrapper (release) | `ghcr.io/unipat-ai/sciencevision-codex-agent:release-20260930-h20` | `sha256:b4c03b16847e7f5d02a4c78e61c89e9f5fe896845663e532ee1f6ae2f8ae04fe` | public |
| Claude Code agent-only wrapper (release) | `ghcr.io/unipat-ai/sciencevision-claude-code-agent:release-20261002-h20` | `sha256:597cc446d579d0ccca961f0eed5b133d7925337fd50008594632117a8fec1b2a` | public |
| Codex + MCP (bundled) | `ghcr.io/unipat-ai/sciencevision-codex-mcp:server-20260930-h20` | `sha256:97ddcbb6bb312176bae8a44f6564f65b6f83fdf381ba6136240bcf10c7e52e8e` | private |
| Claude Code + MCP (bundled) | `ghcr.io/unipat-ai/sciencevision-claude-code-mcp:server-20261002-h20` | `sha256:ac9722518747ac0aae4ff52f8a9dacea5f03c23614d507903db83db98732cd59` | private |
| Private MCP worker (release) | `ghcr.io/unipat-ai/sciencevision-mcp-worker:server-20260924-h20` | `sha256:292e3770e3a7ffd3397f575b9282bd3d65db2a6e3a30ceae838666949a56eafa` | private |
| Deterministic scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20260928b-h20` | `sha256:85607538c3f268200f8c1ffd2b1c4b27805df2f7c31b8fdc04eb478e4813d498` | private |

**Visibility is not set yet — all ten are private today.** The split is the
intent, and it follows from what each image contains. The four private ones
carry repository source: `sci3d_agent/` (the MCP server), `harbor_adapters/`,
and `sci3d_bench/` — the scorer library. The six public ones are source-free,
which for the two wrappers is a stated design property, not an accident.

Setting it is manual. **GitHub exposes no API for package visibility**: the REST
description carries only `GET`, `DELETE` and `POST …/restore` for packages, and
the GraphQL schema only `deletePackageVersion`. An org owner has to open each
package and use Danger Zone → Change visibility, one at a time.

### The Docker Hub copies, and why their digests differ

`suermars/sci3d-*` are still published and still resolve — the move is a
dual-push, nothing was retired. **Their manifest digests are not the GHCR ones**,
and quoting the wrong one is an easy mistake:

| tag `server-20261002-h20` | digest |
| --- | --- |
| Docker Hub `suermars/sci3d-claude-code-2d` | `sha256:5d83f294146d90a68c30f298425f3b53fb9e7d13cabb669e46d6718cb27fad8d` |
| GHCR `unipat-ai/sciencevision-claude-code-2d` | `sha256:c385efa0c471923da9a770c830602a0220d6fa72cbba1278879762a4633b358f` |

The images are nevertheless identical. Both registries hold the same config blob
(`sha256:ea735c5bc8a31684fcccdb52b8d6a80d7912b4cd1bbf56b933c22b9e5fe4afb6`, which
is also the image ID), nine of ten layers are byte-identical, and the tenth is
identical in content but not in compressed form (28.2 MB against 29.1 MB). This
was verified end to end by pulling the GHCR copy and comparing image IDs — same
`sha256:ea735c5b…`, and `claude --version` reports `2.1.287` out of it.

**Quote the digest that belongs to the registry you are pulling from.**

### `:latest`

Only Docker Hub has `:latest` — all six, each resolving to the same digest as
its Docker Hub `server-` tag. **GHCR carries pinned tags only**; no `:latest`
was pushed there. Adding one is a deliberate extra step, worth taking once
visibility is settled.

**But the load-bearing aliases are the local ones on H20, not the `:latest` tags
in any registry** — see §3.2. A registry `:latest` only reaches someone who
explicitly pulls it. The three `codex` ones on Docker Hub were pushed late, on
2026-10-02; the 2026-09-30 round retagged them locally only.

**Cite the pinned tag or the digest.** `:latest` is a mutable name; what it
points at today is not guaranteed tomorrow.

### H20 local aliases (what task Dockerfiles actually resolve)

| Local tag | Points at | Corresponds to |
| --- | --- | --- |
| `sci3d-codex:latest` | `042c1edda6bd` | `server-20260930-h20` |
| `sci3d-codex-2d:latest` | `c26f3f706497` | `server-20260930-h20` |
| `sci3d-codex-mcp:latest` | `1ac62d9e9b91` | `server-20260930-h20` |
| `sci3d-claude-code:latest` | `6033eb1f9a41` | `server-20261002-h20` |
| `sci3d-claude-code-2d:latest` | `ea735c5bc8a3` | `server-20261002-h20` |
| `sci3d-claude-code-mcp:latest` | `1cadef041c4d` | `server-20261002-h20` |

---

## 3. How to use them

### 3.1 Pulling

```bash
docker pull ghcr.io/unipat-ai/sciencevision-claude-code:server-20261002-h20
docker image inspect ghcr.io/unipat-ai/sciencevision-claude-code:server-20261002-h20 \
  --format '{{index .RepoDigests 0}}'
```

`docker login ghcr.io` first. **Every GHCR package is private right now**, so a
pull needs a credential even for the six intended to be public; see §2. On the
eval host the credential is already configured.

To pin an immutable version, pull by digest rather than tag:

```bash
docker pull ghcr.io/unipat-ai/sciencevision-claude-code@sha256:e7e5ce2ce6393acc05f3165c3313b392e26501e29fff3e6fcd3f2f601f4075a5
```

Note `{{index .RepoDigests 0}}` prints **whichever registry you pushed to last**
when an image is tagged for both, so it is not a safe way to check a GHCR pull.
Use the digest that matches the registry you pulled from, and prefer
`docker image inspect <ref> --format '{{index .RepoDigests 0}}'` on the
fully-qualified `ghcr.io/...` reference.

If the digest does not match: **you do not have the version this page
describes — do not run it.** Record the digest you actually got and decide from
there.

### 3.2 As the base of a task Dockerfile (this is the step that lands an image update)

Every task Dockerfile the harness generates uses an **unqualified `:latest`**:

```text
DF_CLAUDE_CODE       -> FROM sci3d-claude-code:latest
DF_CLAUDE_CODE_MCP   -> FROM sci3d-claude-code-mcp:latest
DF_CLAUDE_CODE_2D    -> FROM sci3d-claude-code-2d:latest
DF_CLAUDE_CODE_TEXT  -> FROM sci3d-claude-code-2d:latest
```

(`scripts/harbor/_agent_envs.py`; both lines carry a table of the same shape.)

**Therefore**: building a new image without retagging the local H20 alias
changes nothing — tasks still build from the old base. Conversely, **the last
step of an image update is retagging the local alias, not pushing.**

An unqualified `FROM sci3d-claude-code:latest` resolves against the local image
store first and only falls back to `library/` on Docker Hub (where nothing
exists). So on H20, the local alias is the whole story.

### 3.3 Running an evaluation

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
    -u HTTPS_PROXY -u https_proxy \
    harbor run \
    --path scripts/harbor/tasks-<tree> \
    --agent <claude-code|codex> \
    --model <model> \
    --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
    --ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \
    --ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" \
    --ae "SCI3D_JUDGE_MODEL=claude-haiku-4-5" \
    --env docker \
    -o jobs-<name>
```

- **`SCI3D_JUDGE_MODEL` is mandatory.** The verifier's LLM judge defaults to the
  dated id `claude-haiku-4-5-20251001`, which some gateways do not serve. When
  the judge 503s, `llm_judge` **and** `process_judge` both collapse to `0.0` and
  the reward falls to the `0.100` process floor — and it does so **uniformly
  across every harness**, which reads like "all the models are about the same".
- The proxy variables must be unset (`-u ...`) or httpx's Anthropic transport
  cannot reach the endpoint.
- Per-arm parameters are in the matching runbook under `docs/harbor/`.

### 3.4 Per-image container contract

Pick from this table, not from the name.

| Image | Entry point | Where assets live | Network | Started by |
| --- | --- | --- | --- | --- |
| `sci3d-codex` / `sci3d-claude-code` | the CLI | task Dockerfile runs `COPY asset/ /data/` | outbound to the gateway | harbor |
| `…-2d` | same | pre-rendered view PNGs (`COPY views/ /data/`) | same | harbor |
| `…-mcp` (run line) | CLI + bundled `sciencevision-mcp` (stdio) | `COPY asset/ /data/` | same | harbor |
| wrapper (release) | CLI only — **no MCP server and no evaluator source in the image** | none | outbound, plus the worker | harbor |
| `sci3d-mcp-worker` | `mcp_worker_entrypoint.py`, UID 10001, `EXPOSE 8765` | per-task `/data` plus `/etc/sciencevision-mcp/task-assets.json` | `127.0.0.1:8765/mcp` inside Harbor's shared network namespace | harbor (**do not start it by hand**) |
| `sci3d-scorer` | see §3.5, UID 10001 | none; inputs are read-only mounts | **`--network=none`** | you, or harbor |

### 3.5a An MCP route needs `--mcp-config`, or it silently runs as `cc`

The `-mcp` images carry the MCP server. That is not the same thing as the CLI
*registering* it: started by hand, the CLI waits for a confirmation nobody is
there to give, and the run proceeds **without MCP** — producing `cc` numbers
under a `ccmcp` label, with nothing in the counters to show it.

Harbor's `--mcp-config <path>` is the registration path. Both harnesses pass it
for `ccmcp`, and only for `ccmcp`:

```text
scripts/eval/run-v9-claudecode.sh:210   [[ "$ROUTE" == "ccmcp" ]] && mcp_args=(--mcp-config "$MCP_CONFIG")
scripts/harbor/run_end2end.py:188       cmd += ["--mcp-config", str(MCP_CONFIG)]
```

The file is `scripts/harbor/templates/claude_code_mcp.mcp.json`:

```json
{"mcpServers": {"sciencevision": {"command": "sciencevision-mcp", "args": []}}}
```

Two consequences. `BASELINES.md`'s "registered via harbor's native
`--mcp-config` (user-scoped, no trust dialog)" holds on the harbor path only —
a hand-rolled `docker run` has no such path and hits the dialog. And a `ccmcp`
run whose trajectory contains no `mcp__sciencevision__*` call did not use MCP,
whatever its label says.

### 3.5 Running the scorer standalone

```bash
docker run --rm --network=none --read-only --cap-drop=ALL \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -v "$PWD/task.toml:/work/task.toml:ro" \
  -v "$PWD/answer.json:/work/answer.json:ro" \
  ghcr.io/unipat-ai/sciencevision-scorer:server-20260928b-h20 \
  --task /work/task.toml --answer /work/answer.json
```

`--network=none --read-only --cap-drop=ALL` is part of its contract, not a
hardening suggestion — dropping them means you are not running this release.

### 3.6 The only-mcp arm has no image of its own

It runs on the **`sci3d-claude-code` image** (with the CLI's tool surface hard
disabled), and its "model" lives in the LiteLLM bridge's `model_list`:

```text
<run-root>/bridge/multi-proxy-sv.yaml    # bind-mounted as /etc/pbx-seed-proxy.yaml
```

After editing that file, `docker restart sv-model-bridge` is the entire deploy
step. Two things break it if you change them:

- The provider prefix must stay `hosted_vllm/`. With `openai/`, litellm routes
  `/v1/messages` through the Responses adapter, which never reads
  `finish_reason`, so a truncation is disguised as `stop_reason=end_turn`.
- `litellm_settings.drop_params: true` **silently drops** parameters the upstream
  rejects. So `reasoning_effort` is a request, not a guarantee — verify by
  checking that the response carries a non-zero `reasoning_tokens` (measured:
  21 for `gpt-6.1-sol`, 14 for `gpt-6-astra`), not by reading the config.

---

## 4. Which image for which arm

| Arm | Image |
| --- | --- |
| `cc` (Codex, bare) | `sci3d-codex:server-20260930-h20` |
| `ccmcp` (v9/250 line) | `sci3d-codex-mcp:server-20260930-h20` |
| `ccmcp` (release harness) | wrapper `sci3d-codex-mcp:release-20260930-h20` + worker |
| `cc2d-1/3/6/12` | `sci3d-codex-2d:server-20260930-h20` |
| `cc` (Claude Code, bare) | `sci3d-claude-code:server-20261002-h20` |
| `ccmcp` (Claude Code, v9/250) | `sci3d-claude-code-mcp:server-20261002-h20` |
| `ccmcp` (Claude Code, release) | wrapper `sci3d-claude-code-mcp:release-20261002-h20` + worker |
| `cc2d-*` (Claude Code) | `sci3d-claude-code-2d:server-20261002-h20` |
| only-mcp | `sci3d-claude-code:server-20261002-h20` + bridge config (§3.6) |

**One constraint you have to know**: the two lines do **not** expose the same MCP
tool surface. Both `-mcp` images on this page measure **73 tools** (that is what
§5 step 3 prints); the release-line worker is recorded in `IMAGE_INVENTORY.md` as
**58**, with a different set of names. Before comparing ccmcp numbers across
lines, read "Two lines, one surface rule" in that document.

---

## 5. Self-check

Thirty seconds before a run. All four failure classes below are silent:

```bash
G=ghcr.io/unipat-ai

# 1. Is the version right? (the version in the image table)
docker run --rm --entrypoint codex  $G/sciencevision-codex:server-20260930-h20 --version        # expect 0.159.3
docker run --rm --entrypoint claude $G/sciencevision-claude-code:server-20261002-h20 --version # expect 2.1.287

# 2. Does it know the model? (the table is compiled into the binary; neither an
#    import check nor a version check can catch this)
docker run --rm --entrypoint sh $G/sciencevision-codex:server-20260930-h20 \
  -c 'codex debug models --bundled | grep -q "gpt-6.1-sol" && echo OK'
docker run --rm --entrypoint sh $G/sciencevision-claude-code:server-20261002-h20 \
  -c 'grep -raq claude-opus-4-8 /usr/local/lib/node_modules/@anthropic-ai/claude-code/ && echo OK'

# 3. Is the MCP surface there? (only on the -mcp images)
docker run --rm --entrypoint sciencevision-mcp \
  $G/sciencevision-claude-code-mcp:server-20261002-h20 --list-tools | jq '.tools | length'

# 4. Does the wrapper leak source? (must be empty)
docker run --rm --entrypoint sh $G/sciencevision-claude-code-agent:release-20261002-h20 \
  -c 'ls /app/sci3d_agent 2>/dev/null && echo LEAK || echo clean'
```

**Step 2 is the only check that catches "the CLI does not know the model under
test".** Both CLIs start and answer normally either way: Codex `warn!`s once on
an unknown slug and substitutes fallback metadata (a 272k context window, 10 KB
truncation, `supported_reasoning_levels: []`); Claude Code simply does not have
the model in its table. The build already carries one assertion of this shape
per image.

How to report a failure: the **actual digest**, the `--version` output, and the
raw output of step 2. "It came out about the same" is not evidence.

---

## 6. Rollback

Every previous image is still present; no tag has been deleted.

The rollback commands change the **unqualified local aliases on H20** (§3.2 —
those are what task Dockerfiles resolve):

```bash
docker tag 8eb9bd119993 sci3d-claude-code:latest        # -> 20260926
docker tag 5bcf4ab505a2 sci3d-claude-code-2d:latest     # -> 20260926
docker tag d4e732f7ec9d sci3d-claude-code-mcp:latest    # -> 20260928

docker tag 3248db009ea9 sci3d-codex:latest              # -> 20260922
docker tag 3a61dd649653 sci3d-codex-2d:latest           # -> 20260922
docker tag 28fb61fbfbf6 sci3d-codex-mcp:latest          # -> 20260928
```

To roll the Hub `:latest` aliases back as well, add
`docker tag <id> suermars/<repo>:latest && docker push suermars/<repo>:latest`
for each of the six.

Every historical Docker Hub tag (`server-20260922-h20`, `server-20260926-h20`,
`server-20260928-h20`, `server-20260930-h20`) is also still present and can be
pulled by tag or digest. **GHCR currently holds only the current tag per
image** — the migration re-pushed the live version, not the history — so roll
back to an older version on Docker Hub, or re-push it to GHCR deliberately.
