# Image filesystem layout and trust boundaries

## Boundary overview

~~~text
agent-only wrapper                     private MCP worker
------------------                     ------------------
/app                                  /opt/sciencevision-src
  Codex or Claude CLI                   sci3d_agent/
  HTTP MCP configuration                harbor_adapters/
  no evaluator source                   sci3d_bench/
  no viewer/ or datasets/               viewer/assets.json
                                      datasets/_matched/
                                      /data/ per-task assets only

formal scorer
-------------
/opt/sci3d-scorer
  explicit deterministic scorer allow-list only
  scorer_manifest.json
  no agent, MCP, viewer, or dataset source
~~~

The worker is a separate service. The model-facing agent talks to it through
the HTTP MCP surface, normally http://127.0.0.1:8765/mcp only when Harbor's
verified shared-network-namespace overlay is in effect. Do not restore a
same-container stdio server merely to make localhost work.

## Private MCP worker

The base worker context is assembled only by
scripts/harbor/templates/build_sciencevision_mcp_worker_image.sh. Its
allow-list is:

~~~text
pyproject.toml
README.md
sci3d_agent/
harbor_adapters/
sci3d_bench/
viewer/assets.json
datasets/_matched/
Dockerfile and worker entrypoint/healthcheck templates
~~~

viewer/assets.json and datasets/_matched/ are a legacy compatibility catalog
for sv_get_asset and sv_search_matched_qa. They are private to the worker. Do
not broaden either copy to viewer/ or datasets/; specifically, do not
introduce raw files named by the asset registry or benchmark dataset trees.

The worker must run as sciencevision (UID 10001). Its Dockerfile must give
that user ownership of /opt/sciencevision-src and /opt/sciencevision/bin.
This is not cosmetic: a root-only build-time sciencevision-mcp --list-tools
check can succeed while the non-root worker fails to import a source file at
runtime.

For each task, the worker receives raw task assets under /data and a private
/etc/sciencevision-mcp/task-assets.json manifest. The agent image must not
receive this asset copy.

## Agent-only wrappers

Dockerfile.codex-mcp and Dockerfile.claude-code-mcp use their template
directory as Docker context. They may provide an MCP endpoint environment
variable, but must never copy or install:

~~~text
sci3d_agent/
sci3d_bench/
harbor_adapters/
viewer/
datasets/
sciencevision-mcp
~~~

The wrapper's base CLI and inspection dependencies are allowed. The MCP
worker, not the agent, owns server code, registry data, rendering cache, and
task assets.

## Formal scorer

scorer_bundle/build_context.py constructs the only scorer Docker context. It
copies an explicit formal-scoring allow-list and writes scorer_manifest.json.
The scorer image is non-root (UID 10001) and uses no network during Docker
build or runtime. It must not contain agent/MCP client or server code, raw
assets, viewer code, private masks, or dataset content.

