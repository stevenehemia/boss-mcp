#!/usr/bin/env bash
# Live-agent data representation format efficiency evaluation
#
# Formats:
#     columnar    columnarjson          column-major
#     indexed     indexedcolumnarjson   columnar with a row index per cell
#     positional  positionalrowsjson    schema once, rows as value-tuples (NSM)
#     objects     arrayofobjectsjson    one object per row (column names repeat)
#
# Usage: ./run_efficiency_eval.sh [FILTER ...]
#   Each FILTER is a condition (columnar indexed positional objects) or a
#   question id (XS S M L XL XXL XXXL).
#     ./run_efficiency_eval.sh                # every cell, every replicate
#     ./run_efficiency_eval.sh indexed        # all questions in one condition
#     ./run_efficiency_eval.sh T1             # one tier across all formats
#     ./run_efficiency_eval.sh objects T4     # a single cell, all replicates
#   Cells whose results/<condition>/<id>_rep<N>.json already exists are SKIPPED.
#   Set FORCE=1 to re-run them.
#
# Tool-availability conditions:
#     ./run_efficiency_eval.sh                     # no built-in tools visible
#     FRICTION=1 ./run_efficiency_eval.sh          # tools visibile but not allowed
#     FULL_TOOLS=1 ./run_efficiency_eval.sh        # tools visible and allowed
#
# Transcripts are the default (`--output-format stream-json --verbose`).
# STREAM=0 for the compact one-object-per-cell output.

set -euo pipefail
cd "$(dirname "$0")"

# RESULTS_ROOT is the base name; the tool-condition suffix is appended below.
# Override it to put the results somewhere else
RESULTS_ROOT="${RESULTS_ROOT:-results}"
if [[ "${STREAM:-1}" != "0" ]]; then
    STREAM_ARGS=(--output-format stream-json --verbose)
    echo "STREAM=1: verbose output (save tool-call record)."
else
    STREAM_ARGS=(--output-format json)
    echo "STREAM=0: compact output (no tool-call record)."
fi

# Xlean up generated temp files on exit
trap 'rm -f questions.tsv prompt.arrayjson.txt .mcp.*.json' EXIT

PY="${PYTHON:-python3}"

MODEL="${MODEL:-sonnet}"
EFFORT="${EFFORT:-high}"
MODEL_ARGS=(--model "$MODEL" --effort "$EFFORT")
echo "Using --model $MODEL --effort $EFFORT (override with MODEL= / EFFORT=)"

THINKING="${THINKING:-on}"
if [[ "$THINKING" == "on" ]]; then
    THINKING_ARGS=(--thinking enabled)
else
    THINKING_ARGS=(--thinking disabled)
fi
echo "Using --thinking $THINKING (override with THINKING=on/off)"

# Each tool-availability condition sets its own results directory suffix,
# preventing collisions
BOSS_TOOLS="mcp__boss__boss_evaluate,mcp__boss__boss_describe,ToolSearch"
if [[ -n "${FRICTION:-}" ]]; then
    TOOLS_ARGS=()
    ALLOWED_TOOLS_ARGS=(--allowedTools "$BOSS_TOOLS")
    RESULTS_ROOT="${RESULTS_ROOT}_friction"
    echo "FRICTION=1: built-in tools visible but not allowed."
elif [[ -n "${FULL_TOOLS:-}" ]]; then
    TOOLS_ARGS=()
    ALLOWED_TOOLS_ARGS=(--allowedTools "$BOSS_TOOLS,Bash,Read,Write,Glob,Grep")
    RESULTS_ROOT="${RESULTS_ROOT}_fulltools"
    echo "FULL_TOOLS=1: Bash/Read/Write/Glob/Grep allowed."
else
    TOOLS_ARGS=(--tools "")
    ALLOWED_TOOLS_ARGS=(--allowedTools "$BOSS_TOOLS")
    echo "Built-in tools hidden (--tools \"\")."
fi
export RESULTS_DIR="$RESULTS_ROOT"

BOSS_EXE="$(realpath ../../build/boss_mcp 2>/dev/null || true)"
if [[ ! -x "$BOSS_EXE" ]]; then
    echo "error: boss_mcp binary not found or not executable at ../../build/boss_mcp" >&2
    echo "       Build it first: (cd ../.. && ./build.sh)" >&2
    exit 1
fi

# Cap the server's address space so any result too big to materialize
# fails with a catchable std::bad_alloc instead of exhausting the WSL VM's RAM.
SERVER_MEM_LIMIT_KB="${SERVER_MEM_LIMIT_KB:-8388608}"   # 8 GB

# claude.exe (Windows) cannot exec a Linux ELF directly,
# so the MCP server is spawned through `wsl`
gen_config() {
    local extra=""
    if [[ "${3:-0}" -gt 0 ]]; then
        extra=" --max-result-size-chars=$3"
    fi
    cat > "$1" <<EOF
{
  "mcpServers": {
    "boss": {
      "type": "stdio",
      "command": "wsl",
      "args": ["bash", "-c", "ulimit -v $SERVER_MEM_LIMIT_KB; exec '$BOSS_EXE' --query-format=arrayjson --result-format=$2 --default-thinking=$THINKING$extra"],
      "env": {}
    }
  }
}
EOF
}

# QUESTIONS_MODULE selects the external Python module that defines the questions
export QUESTIONS_MODULE="${QUESTIONS_MODULE:-questions}"

# GROUND_TRUTH is the JSON file containing the correct answers for scoring
export GROUND_TRUTH="${GROUND_TRUTH:-ground_truth.json}"
[[ "$QUESTIONS_MODULE" != "questions" ]] && \
    echo "Ladder: $QUESTIONS_MODULE (ground truth: $GROUND_TRUTH)"

"$PY" - > questions.tsv <<PYEOF
import importlib
for q in importlib.import_module("$QUESTIONS_MODULE").QUESTIONS:
    conds = ",".join(q["conditions"])
    print(f"{q['id']}\t{q['shape']}\t{conds}\t{q['text']}")
PYEOF

mapfile -t COND_LINES < <("$PY" -c "
import importlib
_q = importlib.import_module('$QUESTIONS_MODULE')
for k, v in _q.CONDITIONS.items():
    print(f'{k} {v} {_q.CONDITION_BUDGETS.get(k, 0)}')
")

# Generate evaluation's system prompt
PROMPT_FILE="$(pwd)/prompt.arrayjson.txt"
"$PY" make_prompt.py > "$PROMPT_FILE"

REPLICATES="${REPLICATES:-$("$PY" -c "import importlib; print(importlib.import_module('$QUESTIONS_MODULE').REPLICATES)")}"
echo "Replicates per cell: $REPLICATES"

VALID_CONDS=" $(printf '%s ' "${COND_LINES[@]%% *}")"
VALID_QIDS="$(cut -f1 questions.tsv | tr '\n' ' ')"

COND_FILTER=(); Q_FILTER=()
for arg in "$@"; do
    if [[ "$VALID_CONDS" == *" $arg "* ]]; then
        COND_FILTER+=("$arg")
    elif [[ " $VALID_QIDS " == *" $arg "* ]]; then
        Q_FILTER+=("$arg")
    else
        echo "error: unknown filter '$arg'" >&2
        echo "       conditions:$VALID_CONDS" >&2
        echo "       question ids: $VALID_QIDS" >&2
        exit 1
    fi
done

# True if the filter list is empty (no restriction) or contains the value.
selected() {  # selected <value> <filter-values...>
    local v="$1"; shift
    [[ $# -eq 0 ]] && return 0
    local x; for x in "$@"; do [[ "$x" == "$v" ]] && return 0; done
    return 1
}

ran=0; skipped=0
for line in "${COND_LINES[@]}"; do
    read -r COND RF BUDGET <<< "$line"
    selected "$COND" "${COND_FILTER[@]}" || continue

    if [[ "$RF" == "auto" && "${BUDGET:-0}" -eq 0 ]]; then
        echo "error: condition '$COND' uses --result-format=auto but declares no budget." >&2
        echo "       Add it to ${QUESTIONS_MODULE:-questions}.CONDITION_BUDGETS" >&2
        exit 1
    fi

    echo
    echo "==================== CONDITION: $COND  (result-format=$RF${BUDGET:+, budget=$BUDGET chars}) ===================="
    MCP_CONFIG="$(pwd)/.mcp.$COND.json"
    OUT_DIR="$RESULTS_ROOT/$COND"
    mkdir -p "$OUT_DIR"
    gen_config "$MCP_CONFIG" "$RF" "$BUDGET"

    while IFS=$'\t' read -r id shape conds text; do
        selected "$id" "${Q_FILTER[@]}" || continue
        [[ ",$conds," == *",$COND,"* ]] || continue

        for ((r = 1; r <= REPLICATES; r++)); do
            OUT="$OUT_DIR/${id}_rep${r}.json"
            if [[ -s "$OUT" && -z "${FORCE:-}" ]]; then
                echo "[$COND/$id] ($shape) rep $r/$REPLICATES exists — skipping (FORCE=1 to re-run)"
                skipped=$((skipped + 1)); continue
            fi
            echo "[$COND/$id] ($shape) rep $r/$REPLICATES running..."
            claude --system-prompt-file "$PROMPT_FILE" \
                   "${STREAM_ARGS[@]}" \
                   --mcp-config "$MCP_CONFIG" \
                   --strict-mcp-config \
                   "${TOOLS_ARGS[@]}" \
                   "${ALLOWED_TOOLS_ARGS[@]}" \
                   "${MODEL_ARGS[@]}" \
                   "${THINKING_ARGS[@]}" \
                   -p "$text" < /dev/null > "$OUT"
            ran=$((ran + 1))
            sleep 1
        done
    done < questions.tsv
done

echo
echo "Ran $ran cell(s), skipped $skipped existing."

echo
echo "Scoring and building comparison..."
"$PY" analyse_results.py

echo
echo "Done. Raw per-question output in $RESULTS_ROOT/<condition>/*.json"
