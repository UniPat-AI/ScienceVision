# API reference

> Auto-generated index of the 56 MCP tools, with input/output schemas.

> Status: **draft / scaffold**. The first version will be generated from `python -m sci3d_agent.mcp.server --list-tools` once the code is mirrored.

## Categories

- A — session and discovery
- B — registry
- C — inspectors (volumes, meshes, scenes)
- D — renderers
- E — interactions
- F — snapshots

## How to generate locally

```bash
python -m sci3d_agent.mcp.server --list-tools | jq '.tools[] | {name, description}'
```

A polished form of this output, with schemas and examples, will live here.
