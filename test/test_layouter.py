#!/usr/bin/env python3
"""
Property tests for --result-format=auto (src/layouter.cpp).
Needs ../build/boss_mcp and ../data/owid-covid-data-full.csv.
"""

import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "..", "build", "boss_mcp")
DATA = os.path.join(HERE, "..", "data", "owid-covid-data-full.csv")

# src/main.cpp serves to (1 - BUDGET_MARGIN) x the advertised cap
BUDGET_MARGIN = 0.2

# One fixed cap, chosen below every fixed format's whole size so nothing
# fits and the layouter must page (sections 4 and 8)
PAGE_BUDGET = 1000


def serving_budget(cap):
    return max(1, int(cap * (1.0 - BUDGET_MARGIN)))


def cap_for(serving):
    """The smallest advertised cap whose serving budget is at least `serving`,
    so a budget can be named by where the server will actually cut."""
    cap = math.ceil(serving / (1.0 - BUDGET_MARGIN))
    while serving_budget(cap) < serving:
        cap += 1
    return cap


FIXED_FORMATS = ["columnarjson", "indexedcolumnarjson", "positionalrowsjson",
                 "arrayofobjectsjson"]
TASKS = [None, "lookup", "extremum", "aggregate"]

# Expected winner per intent when every candidate fits whole
EXPECTED_WHEN_ALL_FIT = {
    None: "indexedcolumnar",
    "lookup": "positionalrows",
    "extremum": "indexedcolumnar",
    "aggregate": "columnar",
}


# == transport ================================================================

def _read(proc):
    length = None
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break
        parts = line.split(b":", 1)
        if len(parts) == 2 and parts[0].strip().lower() == b"content-length":
            length = int(parts[1])
    return json.loads(proc.stdout.read(length)) if length else None


def _send(proc, msg):
    body = json.dumps(msg).encode()
    proc.stdin.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    proc.stdin.flush()


def _reply(proc, rid):
    """The reply to request `rid`, skipping notifications -- the layouter emits
    one when a result is over budget."""
    while (m := _read(proc)) and m.get("id") != rid:
        pass
    return m


def _evaluate(args, expression, intent):
    """One boss_evaluate call against a freshly started server."""
    proc = subprocess.Popen([EXE] + args, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        _reply(proc, 1)
        _send(proc, {"jsonrpc": "2.0", "method": "initialized"})

        arguments = {"expression": expression}
        if intent:
            arguments["response_intent"] = intent
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "boss_evaluate", "arguments": arguments}})
        return _reply(proc, 2)["result"]["content"][0]["text"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise


_memo = {}


def evaluate(args, expression, intent=None):
    """Result text of one boss_evaluate call. Identical calls are answered from
    a cache"""
    key = (tuple(args), json.dumps(expression), intent)
    if key not in _memo:
        _memo[key] = _evaluate(args, expression, intent)
    return _memo[key]


def auto(budget, expression, intent=None):
    return evaluate(["--result-format=auto", f"--max-result-size-chars={budget}"],
                    expression, intent)


def fixed(fmt, expression, budget=None):
    args = [f"--result-format={fmt}"]
    if budget is not None:
        args.append(f"--max-result-size-chars={budget}")
    return evaluate(args, expression)


def identify(parsed):
    """Which layout a parsed result is, from structure rather than string
    matching"""
    if not isinstance(parsed, list) or not parsed:
        return "empty"
    head = parsed[0]
    if isinstance(head, dict):
        return "arrayofobjects"
    if isinstance(head, str):
        return "typedcolumnar"
    if isinstance(head, list):
        if head and head[0] == "Schema":
            return "positionalrows"
        if len(head) > 1 and isinstance(head[1], list):
            return "indexedcolumnar"
    return "columnar"


def is_envelope(parsed):
    """True if the server wrapped the result in the pagination envelope"""
    return isinstance(parsed, dict) and "table" in parsed and "overbudget_row_count" in parsed


def dump_like_server(obj):
    """Match nlohmann::json's default dump() exactly, so a manually-built
    envelope is byte-comparable to what the server actually sends."""
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


COLS = ("date", "new_cases_smoothed_per_million", "hosp_patients_per_million")


def query(rows, cols=COLS):
    return ["Project",
             ["Slice",
               ["Filter",
                 ["Load", ["String", DATA]], ["Equal", ["Symbol", "code"], ["String", "ITA"]]],
               ["Int", 0], ["Int", rows]],
            *[["Symbol", c] for c in cols]]


# == checks ===================================================================

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))


def label(task):
    return f"intent={task or '(none)'}"


def main():
    if not os.path.exists(EXE):
        print(f"error: {EXE} not found; run ./build.sh first", file=sys.stderr)
        return 2

    q = query(400)

    # Measure every fixed format once; every budget below is derived from these.
    sizes = {fmt: len(fixed(fmt, q)) for fmt in FIXED_FORMATS}
    smallest, largest = min(sizes.values()), max(sizes.values())
    print("Fixed-format sizes: " + ", ".join(f"{f.replace('json','')}={s:,}" for f, s in sizes.items()))

    # One auto sweep from "everything fits" down to "only the smallest fits".
    budgets = sorted({cap_for(s) for s in (largest + 1000, largest, (largest + smallest) // 2,
                                            smallest + 100, smallest)}, reverse=True)
    print(f"Auto sweep: {len(budgets)} budgets x {len(TASKS)} intents = "
          f"{len(budgets) * len(TASKS)} calls ({', '.join(f'{b:,}' for b in budgets)}) ...")
    served = {(budget, task): auto(budget, q, task) for budget in budgets for task in TASKS}
    print()

    print("1. Serve the expected format when every candidate fits")
    for task in TASKS:
        got = identify(json.loads(served[budgets[0], task]))
        want = EXPECTED_WHEN_ALL_FIT[task]
        check(f"{label(task)} -> {want}", got == want, f"got {got}")

    # Result size can only remain the same or increase due to pagination cost
    # as the budget tightens.
    print("\n2. Results size never increase as the budget tightens")
    for task in TASKS:
        seen = [len(served[budget, task]) for budget in budgets]
        check(f"{label(task)} non-increasing",
              all(a >= b for a, b in zip(seen, seen[1:])), str(seen))

    print("\n3. Paginate results that don't fit whole")
    paged = {task: auto(PAGE_BUDGET, q, task) for task in TASKS}
    for task, text in paged.items():
        envelope = json.loads(text)
        check(f"{label(task)} is paginated, not oversized",
              is_envelope(envelope) and len(text) <= PAGE_BUDGET < smallest,
              f"served {len(text):,} chars, envelope={is_envelope(envelope)}, smallest={smallest:,}")

    print("\n4. Fixed formats ignore the budget")
    for fmt in FIXED_FORMATS:
        n = len(fixed(fmt, q, budget=1000))
        check(f"{fmt} unaffected", n == sizes[fmt], f"{n:,} != {sizes[fmt]:,}")

    # Auto has no layout to choose for a non-Table, fall back to TypedColumnarJSON
    print("\n5. Non-Table results fall back to TypedColumnarJSON")
    text = auto(100_000, ["Plus", ["Integer", 2], ["Integer", 3]])
    check("scalar -> typedcolumnar", identify(json.loads(text)) == "typedcolumnar", text[:60])

    # A paginated result should contain the maximum number of rows that fit in a page
    print("\n6. Fit the maximum number of rows that fit in a page")
    # identify()'s name -> the --result-format flag that produces it.
    FULL_FORMAT_FLAG = {f.removesuffix("json"): f for f in FIXED_FORMATS}

    def rows_in_table(table, fmt):
        if fmt in ("columnar", "indexedcolumnar"):
            return len(table[0]) - 1  # first cell of each column is the header
        if fmt == "positionalrows":
            return len(table) - 1  # first row is ["Schema", ...]
        if fmt == "arrayofobjects":
            return len(table)
        raise ValueError(f"unexpected table format: {fmt}")

    for task in TASKS:
        envelope = json.loads(paged[task])
        fmt = identify(envelope.get("table"))
        shown = rows_in_table(envelope["table"], fmt) if fmt in FULL_FORMAT_FLAG else -1

        check(f"{label(task)} overbudget_row_count == 400 - {shown} = {400 - shown}",
              shown >= 0 and envelope.get("overbudget_row_count") == 400 - shown,
              f"overbudget_row_count={envelope.get('overbudget_row_count')}")
        check(f"{label(task)} shows {shown} rows (0 < rows < 400)", 0 < shown < 400)

        if fmt in FULL_FORMAT_FLAG and 0 < shown < 400:
            bigger_text = fixed(FULL_FORMAT_FLAG[fmt], query(shown + 1))
            bigger_envelope = {"table": json.loads(bigger_text), "overbudget_row_count": 400 - (shown + 1)}
            bigger_len = len(dump_like_server(bigger_envelope))
            check(f"{label(task)} page is full ({shown + 1} rows would exceed serving budget)",
                  bigger_len > serving_budget(PAGE_BUDGET),
                  f"shown+1 -> {bigger_len:,} chars, serving budget={serving_budget(PAGE_BUDGET):,}")

    # A decision can change when the chosen format is paginated and cheaper alternatives exist
    print("\n7. Pagination cost changes a real decision")
    indexed_size = sizes["indexedcolumnarjson"]
    # Scaled so the serving budget lands just above indexed's whole size for
    # the control, just below it for the paged case.
    fits_budget = cap_for(indexed_size + 500)
    pages_budget = cap_for(indexed_size - 600)

    got = identify(json.loads(auto(fits_budget, q, "extremum")))
    check(f"extremum at cap={fits_budget:,} (serving {serving_budget(fits_budget):,} >= "
          f"indexed {indexed_size:,}) -> indexedcolumnar (control)",
          got == "indexedcolumnar", got)

    got = identify(json.loads(auto(pages_budget, q, "extremum")))
    check(f"extremum at cap={pages_budget:,} (serving {serving_budget(pages_budget):,} < "
          f"indexed {indexed_size:,}) -> positionalrows",
          got == "positionalrows", got)

    # The server should return the same format when the agent issues a follow-up retrieval
    print("\n8. A Slice-wrapped follow-up match first page's decision")
    sliced_q = ["Slice", q, ["Int", 300], ["Int", 100]]
    parsed = json.loads(auto(pages_budget, sliced_q, "extremum"))
    sliced_format = identify(parsed["table"] if is_envelope(parsed) else parsed)
    check(f"extremum, Slice(q,300,100) at cap={pages_budget:,} -> positionalrows, "
          f"same as the full table",
          sliced_format == "positionalrows", sliced_format)

    # The same 100 rows behind a top-level Project get no basis and return a different format
    standalone = ["Project", sliced_q, *[["Symbol", c] for c in COLS]]
    parsed = json.loads(auto(pages_budget, standalone, "extremum"))
    standalone_format = identify(parsed["table"] if is_envelope(parsed) else parsed)
    check(f"extremum, Project(Slice(q,300,100)) at cap={pages_budget:,} -> indexedcolumnar "
          f"(no top-level Slice)",
          standalone_format == "indexedcolumnar", standalone_format)

    # IndexedColumnarJSON must continue its label from where page 1 stopped
    print("\n9. IndexedColumnarJSON labels continue across the page boundary")
    cap = cap_for(smallest) - 1

    def indexed_labels(parsed):
        tbl = parsed["table"] if is_envelope(parsed) else parsed
        return [c[0] for c in tbl[0][1:]] if identify(tbl) == "indexedcolumnar" else []

    page1 = json.loads(auto(cap, q, "extremum"))
    l1, withheld = indexed_labels(page1), page1.get("overbudget_row_count", 0)
    check(f"extremum at cap={cap:,}: page 1 is indexed, labels 0..{len(l1) - 1}, {withheld} withheld",
          is_envelope(page1) and l1 == list(range(len(l1))) and len(l1) + withheld == 400,
          f"envelope={is_envelope(page1)}, {len(l1)} labels")

    page2 = json.loads(auto(cap, ["Slice", q, ["Int", len(l1)], ["Int", withheld]], "extremum"))
    l2 = indexed_labels(page2)
    check(f"Slice(q,{len(l1)},{withheld}) -> page 2 labels {len(l1)}..399, contiguous with page 1",
          l1 + l2 == list(range(400)), f"page 2: {len(l2)} labels {l2[:2]}..{l2[-1:]}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
