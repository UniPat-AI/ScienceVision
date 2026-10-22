# Codex agent adapter

The Codex rows in the leaderboard were produced with this adapter, not with Harbor's
built-in `codex` agent. Harbor's agent installs the Codex CLI and registers MCP servers,
but it has no way to point Codex at the gateway that serves these models; this adapter
writes that provider block into `$CODEX_HOME/config.toml` and delegates everything else.

The file is byte-identical to the adapter the reported runs used:

```text
sha256  2859af086445f97fcfb328c0fa4a64aa221d8e9cfb69e8e769fecfb9818abdd4
```

Editing it changes the agent under test. If you must change it, record the new hash in the
run receipt and do not compare the result to rows produced with this one.

## Stage a run

The adapter reads `config.toml` from the directory it lives in and refuses to start when
the provider block does not select `uniapi-sciencevision`, name `UNIAPI_KEY_SCIENCEVISION`,
use `wire_api = "responses"`, or end `base_url` in `/v1`.

```bash
STAGE=/path/to/run/auth/codex
mkdir -p "$STAGE"
cp scripts/agents/sciencevision_codex.py "$STAGE/"
cp scripts/agents/config.toml.example "$STAGE/config.toml"   # then set base_url
chmod 600 "$STAGE/config.toml"
```

The gateway URL and the API key stay out of the repository. Pass the key through the
environment, never on the command line:

```bash
export UNIAPI_KEY_SCIENCEVISION=...
export PYTHONPATH="/path/to/repo:$STAGE"
harbor run --path <task-tree> \
  --agent sciencevision_codex:ScienceVisionUniAPICodex \
  --model gpt-6.1-sol \
  --ak reasoning_effort=max
```

`_ALLOWED_MODELS` in the adapter is the model allow-list. A model outside it raises at
construction time rather than silently running a different agent.

## Settings

Task timeouts, retries, concurrency, MCP registration and image digests are recorded in
[`docs/evaluation.md`](../../docs/evaluation.md#codex-runs). The receipt from a compatible
run records the adapter hash, model, route, reasoning effort, image id and digest, and the
timeout multipliers.
