# Container images and packages

The canonical container namespace is `ghcr.io/unipat-ai`. The twelve release versions below use ten canonical package names. All are private.

[`resources/images.json`](../resources/images.json) records the full tag, immutable GHCR manifest digest, package URL, source mapping, platform, visibility, and verification evidence for every listed version. Verification date: 2026-10-06.

## Naming and release inventory

For each agent engine (`codex` or `claude-code`), the name without a suffix is the raw-file runtime, `-2d` consumes rendered views, `-mcp` bundles the CLI and MCP implementation, and `-mcp-client` connects a client-only CLI wrapper to an external HTTP MCP worker. `sciencevision-mcp-worker` and `sciencevision-scorer` are separate services. These names describe runtime roles, not replacement generations.

| Role | Canonical tag | Package page |
|---|---|---|
| raw-file Codex | `ghcr.io/unipat-ai/sciencevision-codex:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex) |
| rendered-view Codex | `ghcr.io/unipat-ai/sciencevision-codex-2d:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-2d) |
| bundled MCP Codex | `ghcr.io/unipat-ai/sciencevision-codex-mcp:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp) |
| MCP client Codex | `ghcr.io/unipat-ai/sciencevision-codex-mcp-client:release-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp-client) |
| raw-file Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code:server-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code) |
| rendered-view Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code-2d:server-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-2d) |
| bundled MCP Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code-mcp:server-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp) |
| MCP client Claude Code | `ghcr.io/unipat-ai/sciencevision-claude-code-mcp-client:release-20261002-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp-client) |
| external MCP worker | `ghcr.io/unipat-ai/sciencevision-mcp-worker:server-20260924-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-mcp-worker) |
| historical scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20260928b-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) |
| pinned harness scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20260930-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) |
| platform package scorer | `ghcr.io/unipat-ai/sciencevision-scorer:server-20261005-h20` | [Package](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer) |

All inspected images declare `linux/amd64`. The original Docker Hub/GHCR comparison read the manifests and configurations. Corresponding release images have matching configuration digests, including their declared uncompressed layer identities and runtime configuration; their compressed layer and manifest digests differ across registries. That comparison did not download every layer payload or run containers.

The two `-mcp-client` copies transferred the existing GHCR layers and preserve the manifests, configurations, and rootfs layer identities of the `sciencevision-codex-agent` and `sciencevision-claude-code-agent` packages. Those old addresses remain private compatibility aliases, recorded in `legacy_aliases` in the inventory. Use `-mcp-client` for new release references; retain the originally observed address and digest in historical run receipts.

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
- The external HTTP route uses a `sciencevision-*-mcp-client` wrapper and a separate `sciencevision-mcp-worker` service.

These layouts are not interchangeable. Their MCP interfaces, registration, and task contracts must match the selected harness. The worker's `server-20260924-h20` date does not make it redundant: the external HTTP profile still needs a worker, while the bundled profile carries its MCP implementation inside the agent image.

The pinned source still builds and consumes local development aliases such as `sci3d-codex-mcp:latest`, `sci3d-claude-code-mcp:latest`, and `sci3d-mcp-worker:dev`. The two agent aliases can denote a bundled image in one source tree and a client-only wrapper in another. Canonical package naming does not rename those local builds. Some shell preflights check their exact `FROM` strings. Resolve each alias to the intended image identity, verify the agent/worker layout, and retain the mapping in the run receipt. Replacing a `FROM` line alone is not a complete runtime migration.

## Scorer versions

| Version | Use |
|---|---|
| `server-20260930-h20` | Default of the pinned evaluation harness and the oracle validator. The harness pins its GHCR digest. |
| `server-20261005-h20` | Later platform-package scorer. Select it when the prepared task package specifies it. |
| `server-20260928b-h20` | Historical release retained for earlier task contracts and result reproduction. |

The three versions have different configuration digests. A newer date is not evidence that rescoring will preserve a result. Choose the scorer specified by the run contract and save its GHCR digest. A scorer self-test that feeds ground truth back into the scorer is not a model benchmark result.

## Package ownership

Package access is separate from repository access. Future image releases should retain the source revision or staged-source evidence, build receipt, image manifest digest, and task-contract test evidence alongside the resource inventory. Container software and third-party dependencies retain their own licenses.
