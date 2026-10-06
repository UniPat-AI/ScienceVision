# Container images and packages

The canonical container namespace is `ghcr.io/unipat-ai`. Ten existing ScienceVision packages, containing the twelve versions below, are private and linked to [UniPat-AI/ScienceVision](https://github.com/UniPat-AI/ScienceVision). The Docker Hub source copies remain available to authorized users; no source was deleted or tag rewritten during this inventory update.

[`resources/images.json`](../resources/images.json) records the full tag, immutable GHCR manifest digest, package URL, source mapping, platform, visibility, and verification evidence for every listed version. Verification date: 2026-10-06.

## Inventory

| Role | Canonical tag | Package page |
|---|---|---|
| raw-file Codex | `ghcr.io/unipat-ai/sciencevision-codex:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex) |
| rendered-view Codex | `ghcr.io/unipat-ai/sciencevision-codex-2d:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-2d) |
| bundled MCP Codex | `ghcr.io/unipat-ai/sciencevision-codex-mcp:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp) |
| agent-only Codex wrapper | `ghcr.io/unipat-ai/sciencevision-codex-agent:release-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-agent) |
| raw-file Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code:server-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code) |
| rendered-view Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code-2d:server-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-2d) |
| bundled MCP Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code-mcp:server-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp) |
| agent-only Claude Code wrapper | `ghcr.io/unipat-ai/sciencevision-claude-code-agent:release-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-agent) |
| external MCP worker | `ghcr.io/unipat-ai/sciencevision-mcp-worker:server-20260924-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-mcp-worker) |
| historical scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20260928b-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) |
| historical scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) |
| current scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20261005-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) |

All inspected images declare `linux/amd64`. The manifest and configuration digests were read from both registries. Corresponding images have matching configuration digests, including their declared uncompressed layer identities and runtime configuration. Compressed layer and manifest digests differ across registries. Full layer payloads were not downloaded, and no container runtime smoke test was run for this inventory.

## Pull a pinned image

Authenticate with a credential permitted to read the private package, then pull by the GHCR digest. For example, the raw-file Codex image:

```bash
docker login ghcr.io
docker pull ghcr.io/unipat-ai/sciencevision-codex@sha256:43b4bf787e205762368aee65588b7a24bac748f267488769152209632efba04c
```

Use the digest belonging to GHCR. A Docker Hub manifest digest is not interchangeable with it. Avoid `latest` for reproducible evaluation. Readers need both repository and package access.

## Runtime selection

The inventory contains two MCP layouts:

- Bundled images, `sciencevision-*-mcp`, contain a CLI and an MCP implementation.
- The release route uses an agent-only `sciencevision-*-agent` wrapper and a separate `sciencevision-mcp-worker` service.

These layouts are not interchangeable. Their MCP interfaces, registration, and task contracts must match the selected harness. The pinned task builder uses unqualified local base-image aliases and a separate scorer-image setting. Before a run, resolve generated Dockerfiles and service/scorer configuration to the chosen canonical digests; retain that configuration in the run receipt. A manifest audit alone does not establish harness compatibility.

The scorer package has three recorded versions. `server-20261005-h20` is the most recent inspected tag, while the pinned task builder defaults to an older Docker Hub tag. Specify the intended scorer explicitly and run a task-contract smoke check before using its results. A scorer self-test that feeds ground truth back into the scorer is not a model benchmark result.

## Package ownership

All ten package pages are connected to the new private repository. Existing content and visibility were preserved. Future image releases should retain the source revision, build receipt, image manifest digest, and task-contract test evidence alongside the resource inventory. Container software and third-party dependencies retain their own licenses.
