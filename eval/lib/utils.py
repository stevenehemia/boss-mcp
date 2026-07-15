"""Shared utilities for the BOSS eval scripts (token counting, stats,
format conversion, MCP stdio transport).

Consumers outside eval/lib/ add this directory to sys.path before importing
(see baseline_static/eval_baseline_v*.py or baseline_live/boss_pipeline/
eval_liveagent.py for the pattern).
"""

import json
import re
import statistics
import subprocess
from contextlib import contextmanager
from itertools import count as _count
from pathlib import Path
import tiktoken

# This file lives at boss-mcp/eval/lib/utils.py, so the boss-mcp
# checkout root is three parents up. Both the dataset and the BOSS server
# binary live in this checkout (data/ and build/).
_ROOT     = Path(__file__).resolve().parents[2]
DATA_PATH = str(_ROOT / "data" / "owid-covid-data-full.csv")
BOSS_EXE  = str(_ROOT / "build" / "boss_mcp")

_enc = tiktoken.encoding_for_model("gpt-4")
_enc_cache = {}


def _resolve_encoding(name: str):
    """Look up a tiktoken encoding by model name (e.g. 'gpt-4o') or, if that
    isn't recognised (tiktoken's model->encoding table needs a package release
    per new model), by raw encoding name (e.g. 'o200k_base') directly."""
    if name not in _enc_cache:
        try:
            _enc_cache[name] = tiktoken.encoding_for_model(name)
        except KeyError:
            _enc_cache[name] = tiktoken.get_encoding(name)
    return _enc_cache[name]


# ── Token counting ─────────────────────────────────────────────────────────────

def count_tokens_tiktoken(obj, encoding: str = None) -> int:
    """Token count under tiktoken. `encoding` overrides the gpt-4 default --
    pass a model name ('gpt-4o') or raw encoding name ('o200k_base')."""
    text = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    enc = _resolve_encoding(encoding) if encoding else _enc
    return len(enc.encode(text))


def count_tokens_claude(client, obj, model) -> int:
    """Claude's own token count for obj via the free count_tokens endpoint
    (compact-JSON-serialized the same way count_tokens_tiktoken does, so both
    tokenisers measure identical text). `client` is an anthropic.Anthropic
    instance, supplied by the caller."""
    text = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    resp = client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": text}])
    return resp.input_tokens


def word_count(obj) -> int:
    """Count alphanumeric word-tokens in an object's JSON text (splits snake_case
    on underscores)."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return len(re.findall(r"[A-Za-z0-9]+", text))


# ── Statistics ─────────────────────────────────────────────────────────────────

def mean(vals: list) -> float:
    return round(statistics.mean(vals), 3) if vals else 0.0


def stdev(vals: list) -> float:
    try:
        return round(statistics.stdev(vals), 3)
    except statistics.StatisticsError:
        return 0.0


def correlation(xs: list, ys: list) -> float:
    """Pearson correlation coefficient. Returns 0.0 if undefined (<2 points or no variance)."""
    try:
        return round(statistics.correlation(xs, ys), 3)
    except statistics.StatisticsError:
        return 0.0


# ── Report output ──────────────────────────────────────────────────────────────

def save_json(path, data) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_lines(path, lines: list) -> None:
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ── Format conversion ──────────────────────────────────────────────────────────

def expr_to_regular(expr) -> dict:
    """
    Convert ExpressionJSON (array form) to --format=regular JSON (object form).

    ExpressionJSON atoms : ["String","v"] / ["Symbol","v"] / ["Integer",n] / ["Real",n]
    ExpressionJSON ops   : ["Op", arg, ...]
    Regular atoms        : {"type":"string","value":"v"} / {"type":"symbol",...} / etc.
    Regular ops          : {"type":"call","head":"Op","args":[...]}
    """
    if isinstance(expr, bool):  return {"type": "bool",   "value": expr}
    if isinstance(expr, int):   return {"type": "long",   "value": expr}
    if isinstance(expr, float): return {"type": "double", "value": expr}
    if not isinstance(expr, list) or not expr:
        return expr

    op = expr[0]

    # Typed atoms: ["Integer",n], ["Real",n], ["String","s"], ["Symbol","s"], ["Boolean",b]
    if len(expr) == 2 and isinstance(op, str):
        val = expr[1]
        if op == "Integer": return {"type": "long",   "value": val}
        if op == "Real":    return {"type": "double", "value": val}
        if op == "String":  return {"type": "string", "value": val}
        if op == "Symbol":  return {"type": "symbol", "value": val}
        if op == "Boolean": return {"type": "bool",   "value": val}

    # Complex expression: ["Head", arg, ...] or [["Symbol","Head"], arg, ...]
    head = op[1] if (isinstance(op, list) and len(op) == 2 and op[0] == "Symbol") else op
    return {"type": "call", "head": head, "args": [expr_to_regular(a) for a in expr[1:]]}


def expr_depth(expr) -> int:
    """Structural nesting depth of an ExpressionJSON expression tree (atoms = 0)."""
    if not isinstance(expr, list) or not expr:
        return 0
    if len(expr) == 2 and isinstance(expr[0], str) and expr[0] in ("Integer", "Real", "String", "Symbol", "Boolean", "Int"):
        return 0
    return 1 + max((expr_depth(a) for a in expr[1:]), default=0)


def boss_response_to_row_repeated(boss_response_str: str) -> list:
    """
    Convert BOSS ExpressionJSON columnar Table response to row-repeated JSON.

    BOSS format : ["Table", ["col1", v1, v2, ...], ["col2", v1, v2, ...]]
    Row-repeated: [{"col1": v1, "col2": v1}, ...]
    """
    try:
        parsed = json.loads(boss_response_str)
        if not isinstance(parsed, list) or parsed[0] != "Table":
            return []

        columns = {}
        for col_def in parsed[1:]:
            if isinstance(col_def, list) and len(col_def) >= 1:
                columns[col_def[0]] = [
                    v[1] if isinstance(v, list) and len(v) == 2 else v
                    for v in col_def[1:]
                ]

        if not columns:
            return []

        return [dict(zip(columns, row)) for row in zip(*columns.values())]

    except (json.JSONDecodeError, IndexError, KeyError):
        return []


def boss_response_shape(boss_response_str: str, encoding: str = None) -> dict:
    """Extract (rows, cols, avg column-name token length) from a BOSS Table response."""
    try:
        parsed = json.loads(boss_response_str)
        if not isinstance(parsed, list) or parsed[0] != "Table":
            return {"rows": 0, "cols": 0, "avg_colname_tokens": 0.0}

        col_defs = [c for c in parsed[1:] if isinstance(c, list) and c]
        cols     = len(col_defs)
        rows     = len(col_defs[0]) - 1 if col_defs else 0
        avg_colname_tokens = mean([count_tokens_tiktoken(c[0], encoding) for c in col_defs]) if col_defs else 0.0

        return {"rows": rows, "cols": cols, "avg_colname_tokens": avg_colname_tokens}

    except (json.JSONDecodeError, IndexError, KeyError):
        return {"rows": 0, "cols": 0, "avg_colname_tokens": 0.0}


# ── MCP transport ──────────────────────────────────────────────────────────────

def _read_msg(stream):
    content_length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break
        parts = line.split(b":", 1)
        if len(parts) == 2 and parts[0].strip().lower() == b"content-length":
            content_length = int(parts[1].strip())
    if content_length is None:
        return None
    payload = stream.read(content_length)
    return json.loads(payload.decode("utf-8")) if payload else None


def _send_msg(proc, msg):
    payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    header  = b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
    proc.stdin.write(header + payload)
    proc.stdin.flush()


def _wait_for(proc, req_id):
    while True:
        msg = _read_msg(proc.stdout)
        if msg is None:
            raise RuntimeError("boss_mcp server closed stdout unexpectedly")
        if msg.get("id") == req_id:
            return msg


@contextmanager
def boss_session(query_format="arrayjson", result_format="columnarjson"):
    """Start boss_mcp, perform handshake, yield (proc, req_id_generator).

    The default matches the pre-split ExpressionJSON flow. Pass explicit
    formats to compare the native wire encodings.
    """
    proc = subprocess.Popen(
        [BOSS_EXE,f"--query-format={query_format}", f"--result-format={result_format}"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    ids = _count(1)
    init_id = next(ids)
    _send_msg(proc, {"jsonrpc": "2.0", "id": init_id, "method": "initialize", "params": {}})
    _wait_for(proc, init_id)
    _send_msg(proc, {"jsonrpc": "2.0", "method": "initialized"})
    try:
        yield proc, ids
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)


def boss_call(proc, expression, req_id: int) -> dict:
    """Execute one BOSS query. Returns {'success': bool, 'result': str | None, 'error': str | None}."""
    _send_msg(proc, {
        "jsonrpc": "2.0", "id": req_id,
        "method": "tools/call",
        "params": {"name": "boss_evaluate", "arguments": {"expression": expression}},
    })
    resp    = _wait_for(proc, req_id)
    result  = resp.get("result", {})
    content = (result.get("content") or [{}])[0]
    text    = content.get("text", "")
    if result.get("isError"):
        return {"success": False, "result": None, "error": text or "unknown error"}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and parsed and parsed[0] == "ErrorWhenEvaluatingExpression":
            error_msg = parsed[2][1] if len(parsed) > 2 and isinstance(parsed[2], list) else text
            return {"success": False, "result": None, "error": error_msg}
    except (json.JSONDecodeError, IndexError):
        pass
    return {"success": True, "result": text, "error": None}
