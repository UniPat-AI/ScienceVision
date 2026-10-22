# Container images

The canonical container namespace is **`ghcr.io/unipat-ai`**. Ten packages carry fifteen release
versions and one archived experimental runtime: sixteen versions in total, all
`linux/amd64`.

[`resources/images.json`](../resources/images.json) records, for every version, the tag, the
immutable GHCR manifest digest, the config digest, the platform, the package URL, and the
verification evidence. `scripts/verify_images.py` re-reads all of it from GHCR.

## Which runtime

The harness needs an **agent image** for the route being run, and for the external-HTTP route a
**worker** as well. Scoring always uses the **scorer** image named by the task contract.

| Runtime | Package | Current version |
|---|---|---|
| Raw task files, Codex | [`sciencevision-codex`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex) | `server-20260930-h20` |
| Rendered views, Codex | [`sciencevision-codex-2d`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-2d) | `server-20260930-h20` |
| Bundled MCP tools, Codex | [`sciencevision-codex-mcp`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp) | `server-20261014-h20` |
| MCP client, Codex | [`sciencevision-codex-mcp-client`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp-client) | `release-20260930-h20` |
| Raw task files, Claude Code | [`sciencevision-claude-code`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code) | `server-20261002-h20` |
| Rendered views, Claude Code | [`sciencevision-claude-code-2d`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-2d) | `server-20261002-h20` |
| Bundled MCP tools, Claude Code | [`sciencevision-claude-code-mcp`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp) | `server-20261015-h20` |
| MCP client, Claude Code | [`sciencevision-claude-code-mcp-client`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp-client) | `release-20261002-h20` |
| External HTTP MCP worker | [`sciencevision-mcp-worker`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-mcp-worker) | `server-20260924-h20` |
| Deterministic scorer | [`sciencevision-scorer`](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) | `server-20260930-h20` |

The naming is positional, not generational. For each agent engine, the name without a suffix is
the raw-file runtime, `-2d` consumes rendered views, `-mcp` bundles the CLI together with an MCP
implementation, and `-mcp-client` connects a client-only wrapper to an external HTTP worker.

## Bundled or external

There are two MCP layouts, and they are not interchangeable:

- **Bundled.** `sciencevision-*-mcp` carries the CLI and its MCP implementation in one image.
- **External HTTP.** `sciencevision-*-mcp-client` plus a separate `sciencevision-mcp-worker`
  service. The worker's older date does not make it redundant; this layout needs it.

Their MCP interfaces, registration, and task contracts must match the selected harness.

**Package roles are deployment layouts, not access settings.** An `-mcp` package name does not
establish either **3D + Scaffold + Vision MCP** (direct source access retained) or **3D + Vision
MCP** (tools only, sources withheld). Select the runtime against the intended access conditions
in [evaluation.md](evaluation.md#evaluation-settings).

## Scorer versions

| Version | Use |
|---|---|
| `server-20261021-h20` | Reference scorer. Grades the comparators and accepted spellings the 250-task package declares |
| `server-20260930-h20` | Default of the pinned evaluation harness |
| `server-20261005-h20` | Later platform-package scorer; select it when a prepared task package specifies it |
| `server-20260928b-h20` | Historical release retained for earlier task contracts |

The four have different config digests, so **a newer date is not evidence that rescoring will
preserve a result**. Choose the scorer the run contract names and record its digest. A scorer
self-test that feeds ground truth back in is not a model benchmark result.

## Pull a pinned image

Authenticate with a credential that can read the package, then pull **by digest**. Digest
addressing is what makes a run reproducible; a tag can be repointed.

```bash
docker login ghcr.io
docker pull ghcr.io/unipat-ai/sciencevision-codex-mcp@sha256:852f66cc924105c61e5efe6f85e0b5b389e01c416f7e6c8a09fbda0a225a1c98
docker pull ghcr.io/unipat-ai/sciencevision-claude-code-mcp@sha256:74a05ae987097140b97ca976a406cf272866c9edbb6fa2313e897a25dab36df5
docker pull ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3
```

Avoid `latest` for anything you intend to report. Package access is separate from repository
access, so a reader may hold one and not the other.

## Local development aliases

The harness builds and consumes local aliases such as `sci3d-codex-mcp:latest`,
`sci3d-claude-code-mcp:latest`, and `sci3d-mcp-worker:dev`. Some runner preflights check the
exact `FROM` string, and the two agent aliases can denote a bundled image in one source tree and
a client-only wrapper in another. Resolve each alias to the intended image identity, verify the
agent and worker layout, and keep that mapping in the run receipt. Replacing a `FROM` line is
not a runtime migration.

## Archived experimental runtime

`ghcr.io/unipat-ai/sciencevision-claude-code-mcp:mcpfix-20261006-h20` holds MCP tool-surface
patches used by an oracle workflow. It was archived from a locally built image rather than a
registry release, differs from the release tags above, and is not a default reference anywhere
in the pinned baseline. It is kept for reproducibility of that workflow only.

## Verification

The inventory was re-read from GHCR on **2026-10-16** by
[`scripts/verify_images.py`](../scripts/verify_images.py), which fetches every manifest and its
config blob and compares them against the recorded values. A manifest digest addresses one
immutable artifact; the config digest identifies the image content, so equal config digests
establish equal declared uncompressed rootfs layer identities and runtime configuration. The
pass did not download layer payloads or run containers.

Future releases should retain the source revision or staged-source evidence, the build receipt,
the manifest digest, and task-contract test evidence alongside this inventory. Container
software and third-party dependencies keep their own licenses.
