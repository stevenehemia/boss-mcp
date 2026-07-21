#!/usr/bin/env bash
# Live-agent 2x2 format comparison.
#
# Two independent axes, run through `claude -p`:
#   query encoding (what the agent writes): arrayjson vs objectjson
#   result encoding (what the server returns): columnarjson vs rowrepjson
#
# 4 conditions = {arrayjson, objectjson} x {columnarjson, rowrepjson}. Each is
# the BOSS MCP server launched with the matching --query-format/--result-format,
# plus the system prompt for that query format.
#
# Usage: ./run_format_eval.sh [FILTER ...]
#   Each FILTER is either a condition (arr-col arr-row obj-col obj-row) or a
#   question id (e.g. XS S M L). Same-kind filters are OR'd; the two kinds are
#   AND'd. With no filters, the full grid runs.
#     ./run_format_eval.sh              # every cell
#     ./run_format_eval.sh arr-row      # all questions in one condition
#     ./run_format_eval.sh L            # one question across all conditions
#     ./run_format_eval.sh obj-row L    # a single cell
#   Cells whose results/<condition>/<id>.json already exists are SKIPPED, so an
#   interrupted run just resumes. Set FORCE=1 to re-run them.

set -euo pipefail
cd "$(dirname "$0")"

# Always clean up generated temp files (questions.tsv, per-condition prompts,
# per-condition MCP configs) on exit, including early exit from `set -e` on a
# failed `claude` call or Ctrl-C — otherwise an interrupted sweep leaves them
# behind for the next run to trip over.
trap 'rm -f questions.tsv prompt.arrayjson.txt prompt.objectjson.txt .mcp.*.json' EXIT

PY="${PYTHON:-python3}"

MODEL_ARGS=()
if [[ -n "${CLAUDE_MODEL:-}" ]]; then
    MODEL_ARGS=(--model "$CLAUDE_MODEL")
    echo "Using --model $CLAUDE_MODEL for all runs."
fi

BOSS_EXE="$(realpath ../../build/boss_mcp 2>/dev/null || true)"
if [[ ! -x "$BOSS_EXE" ]]; then
    echo "error: boss_mcp binary not found or not executable at ../../build/boss_mcp" >&2
    echo "       Build it first: (cd ../.. && ./build.sh)" >&2
    exit 1
fi

# condition-key  query-format  result-format
ALL_CONDITIONS=(
    "arr-col arrayjson  columnarjson"
    "arr-row arrayjson  rowrepjson"
    "obj-col objectjson columnarjson"
    "obj-row objectjson rowrepjson"
)

# Cap the server's address space so a pathological agent query — e.g. an unkeyed
# Join, which BOSS degrades to an O(n^2) cross-join over ~429k rows, or any result
# too big to materialize — fails with a catchable std::bad_alloc (handlers.cpp
# turns it into an error response) instead of exhausting the WSL VM's RAM and
# taking VSCode connection down with it.
SERVER_MEM_LIMIT_KB="${SERVER_MEM_LIMIT_KB:-8388608}"   # 8 GiB

# claude.exe (Windows) cannot exec a Linux ELF directly, so the MCP server is
# spawned through `wsl` (here via `bash -c`)
gen_config() {
    cat > "$1" <<EOF
{
  "mcpServers": {
    "boss": {
      "type": "stdio",
      "command": "wsl",
      "args": ["bash", "-c", "ulimit -v $SERVER_MEM_LIMIT_KB; exec '$BOSS_EXE' --query-format=$2 --result-format=$3"],
      "env": {}
    }
  }
}
EOF
}

echo "Dumping questions..."
"$PY" - > questions.tsv <<'PYEOF'
from questions import QUESTIONS
for q in QUESTIONS:
    print(f"{q['id']}\t{q['shape']}\t{q['text']}")
PYEOF

VALID_CONDS=" arr-col arr-row obj-col obj-row "
VALID_QIDS="$(cut -f1 questions.tsv | tr '\n' ' ')"

# Parse filters: each arg is a condition key or a question id.
COND_FILTER=(); Q_FILTER=()
for arg in "$@"; do
    if [[ "$VALID_CONDS" == *" $arg "* ]]; then
        COND_FILTER+=("$arg")
    elif [[ " $VALID_QIDS " == *" $arg "* ]]; then
        Q_FILTER+=("$arg")
    else
        echo "error: unknown filter '$arg'" >&2
        echo "       conditions: arr-col arr-row obj-col obj-row" >&2
        echo "       question ids: $VALID_QIDS" >&2
        exit 1
    fi
done

# Called once for a condition (against COND_FILTER) and once for a question
# id (against Q_FILTER). True if that filter list is empty (no restriction --
# everything passes) or explicitly contains this value.
selected() {  # selected <value> <filter-values...>
    local v="$1"; shift
    [[ $# -eq 0 ]] && return 0
    local x; for x in "$@"; do [[ "$x" == "$v" ]] && return 0; done
    return 1
}

for qf in arrayjson objectjson; do
    "$PY" format_prompts.py "$qf" > "prompt.$qf.txt"
done

ran=0; skipped=0
for entry in "${ALL_CONDITIONS[@]}"; do
    read -r COND QF RF <<< "$entry"
    selected "$COND" "${COND_FILTER[@]}" || continue

    echo
    echo "==================== CONDITION: $COND  (query=$QF, result=$RF) ===================="
    MCP_CONFIG="$(pwd)/.mcp.$COND.json"
    PROMPT_FILE="$(pwd)/prompt.$QF.txt"
    OUT_DIR="results/$COND"
    mkdir -p "$OUT_DIR"
    gen_config "$MCP_CONFIG" "$QF" "$RF"

    while IFS=$'\t' read -r id shape text; do
        selected "$id" "${Q_FILTER[@]}" || continue
        OUT="$OUT_DIR/$id.json"
        if [[ -s "$OUT" && -z "${FORCE:-}" ]]; then
            echo "[$COND/$id] ($shape) exists — skipping (FORCE=1 to re-run)"
            skipped=$((skipped + 1)); continue
        fi
        echo "[$COND/$id] ($shape) running..."
        claude --system-prompt-file "$PROMPT_FILE" \
               --output-format json \
               --mcp-config "$MCP_CONFIG" \
               --strict-mcp-config \
               --allowedTools "mcp__boss__boss_evaluate,mcp__boss__boss_describe,ToolSearch" \
               "${MODEL_ARGS[@]}" \
               -p "$text" < /dev/null > "$OUT"
        ran=$((ran + 1))
        sleep 1
    done < questions.tsv
done

echo
echo "Ran $ran cell(s), skipped $skipped existing."

echo
echo "Building comparison..."
"$PY" analyse_results.py

echo
echo "Done. Raw per-question output in results/<condition>/*.json"
