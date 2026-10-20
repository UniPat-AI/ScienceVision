#!/usr/bin/env bash
# Shared Codex preflight/argv builder for the shell evaluation runners.

_CODEX_RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_RUNTIME_MCP_CONFIG="${_CODEX_RUNTIME_DIR}/templates/claude_code_mcp.mcp.json"
# This import-path agent delegates to Harbor's built-in Codex implementation
# after it writes the task-bound route health receipt.  It is used only for
# CC/CC-MCP; CC2D remains the ordinary Codex route and has no MCP contract.
CODEX_ROUTE_CONTRACT_AGENT="harbor_adapters.sci3d.route_codex_agent:RouteContractCodex"

_codex_trim_value() {
  CODEX_TRIMMED_VALUE="$1"
  CODEX_TRIMMED_VALUE="${CODEX_TRIMMED_VALUE#"${CODEX_TRIMMED_VALUE%%[![:space:]]*}"}"
  CODEX_TRIMMED_VALUE="${CODEX_TRIMMED_VALUE%"${CODEX_TRIMMED_VALUE##*[![:space:]]}"}"
}

codex_resolve_harbor() {
  _codex_trim_value "${HARBOR:-}"
  local harbor_override="$CODEX_TRIMMED_VALUE"
  if [ -n "$harbor_override" ]; then
    case "$harbor_override" in
      /*) ;;
      *) echo "ABORT: HARBOR must name an absolute executable file" >&2; return 2 ;;
    esac
    if [ ! -f "$harbor_override" ] || [ ! -x "$harbor_override" ]; then
      echo "ABORT: HARBOR must name an absolute executable file" >&2
      return 2
    fi
    CODEX_HARBOR_BIN="$harbor_override"
    return
  fi

  CODEX_HARBOR_BIN="$(command -v harbor || true)"
  if [ -n "$CODEX_HARBOR_BIN" ]; then
    return
  fi

  local python_bin="${HARBOR_PYTHON:-$(command -v python3 || true)}"
  if [ -n "$python_bin" ]; then
    local candidate
    candidate="$(cd "$(dirname "$python_bin")" 2>/dev/null && pwd)/harbor"
    if [ -f "$candidate" ] && [ -x "$candidate" ]; then
      CODEX_HARBOR_BIN="$candidate"
      return
    fi
  fi
  echo "ABORT: harbor is not on PATH or beside the selected Python" >&2
  return 2
}

codex_runtime_preflight() {  # $1 = job/output root that receives codex-runtime.json
  local record_dir="$1"
  local python_bin="${CODEX_PREFLIGHT_PYTHON:-${HARBOR_PYTHON:-python3}}"
  "$python_bin" -m scripts.harbor._codex_runtime --record-dir "$record_dir" || return

  _codex_trim_value "${CODEX_MODEL:-}"
  CODEX_RUNTIME_MODEL="$CODEX_TRIMMED_VALUE"
  CODEX_COMMON_AGENT_ARGS=(--agent codex --model "$CODEX_RUNTIME_MODEL")
  if [[ "${OPENAI_API_KEY:-}" =~ [^[:space:]] ]]; then
    # Harbor's Codex adapter reads the key from this process environment. Keep
    # the secret out of argv and therefore out of process listings.
    :
  else
    _codex_trim_value "${CODEX_AUTH_JSON_PATH:-}"
    local auth_path="$CODEX_TRIMMED_VALUE"
    if [ -z "$auth_path" ]; then
      CODEX_COMMON_AGENT_ARGS+=(--ae "CODEX_FORCE_AUTH_JSON=1")
      return
    fi
    case "$auth_path" in
      "~") auth_path="$HOME" ;;
      "~/"*) auth_path="$HOME/${auth_path#\~/}" ;;
    esac
    CODEX_COMMON_AGENT_ARGS+=(--ae "CODEX_AUTH_JSON_PATH=$auth_path")
  fi
}

codex_verify_runtime_record() {  # $1 = record dir, $2 = expected model, $3 = python (optional)
  local record_dir="$1" expected_model="$2"
  local python_bin="${3:-${CODEX_PREFLIGHT_PYTHON:-python3}}"
  local record_path="${record_dir}/codex-runtime.json"
  if [ ! -s "$record_path" ]; then
    echo "ABORT: Codex runtime record is missing or empty: $record_path" >&2
    return 2
  fi
  _codex_trim_value "$expected_model"
  CODEX_RUNTIME_MODEL="$CODEX_TRIMMED_VALUE"
  "$python_bin" - "$record_path" "$CODEX_RUNTIME_MODEL" <<'PY'
import json
import sys
from pathlib import Path

record_path = Path(sys.argv[1])
expected_model = sys.argv[2]
try:
    payload = json.loads(record_path.read_text())
except (OSError, json.JSONDecodeError) as exc:
    print(f"ABORT: invalid Codex runtime record {record_path}: {exc}", file=sys.stderr)
    raise SystemExit(2)

if payload.get("agent") != "codex":
    print(f"ABORT: runtime record is not for the Codex agent: {record_path}", file=sys.stderr)
    raise SystemExit(2)
recorded_model = payload.get("model")
if recorded_model != expected_model:
    print(
        f"ABORT: CODEX_MODEL={expected_model!r} does not match "
        f"recorded model {recorded_model!r} in {record_path}",
        file=sys.stderr,
    )
    raise SystemExit(2)
PY
}

codex_args_for_baseline() {  # $1 = cc | ccmcp | cc2d-*
  local baseline="$1"
  CODEX_BASELINE_AGENT_ARGS=("${CODEX_COMMON_AGENT_ARGS[@]}")
  case "$baseline" in
    cc|ccmcp) CODEX_BASELINE_AGENT_ARGS[1]="$CODEX_ROUTE_CONTRACT_AGENT" ;;
  esac
  if [ "$baseline" = "ccmcp" ]; then
    CODEX_BASELINE_AGENT_ARGS+=(--mcp-config "$CODEX_RUNTIME_MCP_CONFIG")
  fi
}

codex_validate_task_tree() {  # $1 = baseline, $2 = staged tree, $3 = expected count (optional)
  local baseline="$1" task_tree="$2" expected expected_count="${3:-}" files=0
  case "$baseline" in
    cc) expected="FROM sci3d-codex:latest" ;;
    ccmcp) expected="FROM sci3d-codex-mcp:latest" ;;
    cc2d|cc2d-*) expected="FROM sci3d-codex-2d:latest" ;;
    *) echo "ABORT: unknown Codex baseline: $baseline" >&2; return 2 ;;
  esac
  if [ ! -d "$task_tree" ]; then
    echo "ABORT: staged task tree is missing: $task_tree" >&2
    return 2
  fi
  while IFS= read -r -d '' dockerfile; do
    files=$((files + 1))
    if ! grep -Fqx "$expected" "$dockerfile"; then
      echo "ABORT: stale non-Codex task image in $dockerfile" >&2
      echo "       regenerate the staged tree before running this baseline" >&2
      return 2
    fi
  done < <(find "$task_tree" -mindepth 3 -maxdepth 3 -path '*/environment/Dockerfile' -print0)
  if [ "$files" -eq 0 ]; then
    echo "ABORT: no staged task Dockerfiles found under $task_tree" >&2
    return 2
  fi
  if [ -n "$expected_count" ] && [ "$files" -ne "$expected_count" ]; then
    echo "ABORT: expected $expected_count staged tasks under $task_tree, found $files" >&2
    return 2
  fi
}
