"""
Shared BOSS-server access for the offline studies.

Lives in eval/lib/ (not inside any one study's directory) because both
accuracy/ and any future offline study import it — see SERVER-IN-THE-LOOP.md.
Consumers outside eval/lib/ add this directory to sys.path before importing
(see accuracy/tables.py for the pattern).

This is the anchor that keeps the MCP server in the loop even when an experiment
doesn't drive a live agent: every table an offline study measures is produced by
a real `boss_evaluate` call against the built server, in the exact format the
server emits — not hand-written JSON. So the artifacts under study are genuinely
"what BOSS hands an agent," and the column-name/type-tag/date/null conventions
are the server's, not an approximation.

Two entry points:

  fetch(expr, query_format, result_format) -> str
      The raw result bytes the server returns — byte-exact for the two native
      formats (`columnarjson`, `rowrepjson`). Use this when you need the literal
      encoding (e.g. token counting on the native formats).

  fetch_table(expr) -> Table
      The same result parsed into a neutral row-major structure (Python-native
      values; BOSS's NULL symbol -> None; dates already ISO strings). Use this as
      the single source of truth for ground-truth computation and for rendering
      *derived* formats (CSV/TSV/Markdown/YAML) that the server doesn't emit.

The server is launched under the same ulimit cap the runner uses, so a
pathological stimulus query fails as a catchable error rather than exhausting the
VM. stdlib-only; no agent, no API key.
"""

import json
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import count
from pathlib import Path

# eval/lib/boss_client.py -> boss-mcp/build/boss_mcp
BOSS_EXE = str((Path(__file__).resolve().parent.parent.parent / "build" / "boss_mcp"))
MEM_LIMIT_KB = 8_388_608  # 8 GiB, matches run_format_eval.sh


class BossError(RuntimeError):
    """A boss_evaluate call returned an error expression instead of a result."""


# ── neutral table ───────────────────────────────────────────────────────────────

@dataclass
class Table:
    columns: list          # column names, in order
    rows: list             # row-major: list of [v0, v1, ...] with Python-native values

    @property
    def ncols(self): return len(self.columns)

    @property
    def nrows(self): return len(self.rows)


def _atom(v):
    """Unwrap a columnar typed atom ["Type", value] to a Python value; NULL -> None."""
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
        kind, val = v
        if kind == "Symbol" and val == "NULL":
            return None
        return val
    return v


def parse_columnar(result: str) -> Table:
    """Parse a columnar `["Table", ["col", v1, ...], ...]` result into a Table."""
    data = json.loads(result)
    if not (isinstance(data, list) and data and data[0] == "Table"):
        raise BossError(f"not a Table result: {result[:160]}")
    col_defs = [c for c in data[1:] if isinstance(c, list) and c]
    columns = [c[0] for c in col_defs]
    col_values = [[_atom(v) for v in c[1:]] for c in col_defs]
    rows = [list(r) for r in zip(*col_values)] if col_values else []
    return Table(columns, rows)


# ── MCP stdio transport ──────────────────────────────────────────────────────────

def _send(proc, msg):
    body = json.dumps(msg, separators=(",", ":")).encode()
    proc.stdin.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    proc.stdin.flush()


def _read(proc):
    length = None
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break
        key, _, val = line.partition(b":")
        if key.strip().lower() == b"content-length":
            length = int(val)
    return json.loads(proc.stdout.read(length)) if length else None


@contextmanager
def session(query_format="arrayjson", result_format="columnarjson", mem_limit_kb=MEM_LIMIT_KB):
    """Launch boss_mcp with the given formats (capped), do the handshake, yield (proc, id_gen)."""
    launch = ["bash", "-c",
              f"ulimit -v {mem_limit_kb}; exec {BOSS_EXE} "
              f"--query-format={query_format} --result-format={result_format}"]
    proc = subprocess.Popen(launch, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ids = count(1)
    init = next(ids)
    _send(proc, {"jsonrpc": "2.0", "id": init, "method": "initialize", "params": {}})
    while (m := _read(proc)) and m.get("id") != init:
        pass
    _send(proc, {"jsonrpc": "2.0", "method": "initialized"})
    try:
        yield proc, ids
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def evaluate(proc, ids, expression) -> str:
    """One boss_evaluate call; returns the result text (may be an error expression)."""
    rid = next(ids)
    _send(proc, {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                 "params": {"name": "boss_evaluate", "arguments": {"expression": expression}}})
    while (m := _read(proc)) and m.get("id") != rid:
        pass
    return m["result"]["content"][0]["text"]


# ── convenience: one query, one shot ─────────────────────────────────────────────

def fetch(expression, query_format="arrayjson", result_format="columnarjson") -> str:
    """Raw server output bytes for one query (byte-exact for the native formats)."""
    with session(query_format, result_format) as (proc, ids):
        return evaluate(proc, ids, expression)


def fetch_table(expression, query_format="arrayjson") -> Table:
    """The query's result as a neutral Table (parsed from the server's columnar output)."""
    return parse_columnar(fetch(expression, query_format, "columnarjson"))
