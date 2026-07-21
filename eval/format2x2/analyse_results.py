#!/usr/bin/env python3
"""
Analyse the live-agent 2x2 format experiment results, generating a summary JSON and
a markdown report.

Reads the per-question `claude -p` outputs that run_format_eval.sh wrote to
  results/<condition>/<id>.json

  query axis (input encoding)  : objectjson vs arrayjson
  result axis (output layout)  : rowrepjson vs columnarjson

Token accounting: per-model usage from `modelUsage` summed across all models.
`total_in` = inputTokens + cacheReadInputTokens + cacheCreationInputTokens — the
cache-read term is where a verbose query/response surfaces.

Aggregates use the set of questions that succeeded in ALL FOUR conditions, so
every comparison is over the same questions. Ratios > 1 mean the named variant
costs more.

Outputs:
  results/summary.json
  results/comparison.md
"""

import json
import os
import sys
from datetime import datetime, timezone

from questions import QUESTIONS, SHAPES

# condition key -> (query format, result format)
CONDITIONS = {
    "arr-col": ("arrayjson",  "columnarjson"),
    "arr-row": ("arrayjson",  "rowrepjson"),
    "obj-col": ("objectjson", "columnarjson"),
    "obj-row": ("objectjson", "rowrepjson"),
}
COND_KEYS = list(CONDITIONS)
OUT_DIR = "results"


def extract(path):
    """Comparable metrics out of one `claude -p --output-format json` file."""
    with open(path) as f:
        d = json.load(f)
    mu = d.get("modelUsage", {}) or {}

    def s(field):
        return sum(m.get(field, 0) for m in mu.values())

    in_raw, cr, cc, out = s("inputTokens"), s("cacheReadInputTokens"), s("cacheCreationInputTokens"), s("outputTokens")
    return {
        "ok": not d.get("is_error", False),
        "turns": d.get("num_turns", 0),
        "cost": d.get("total_cost_usd", 0.0),
        "cache_read": cr,
        "out": out,
        "total_in": in_raw + cr + cc,
    }


def collect():
    """{id: {shape, <cond>: metrics|None, ...}}"""
    rows = {}
    for q in QUESTIONS:
        entry = {"shape": q["shape"]}
        for cond in COND_KEYS:
            path = os.path.join(OUT_DIR, cond, f"{q['id']}.json")
            entry[cond] = extract(path) if os.path.exists(path) else None
        rows[q["id"]] = entry
    return rows


def ratio(a, b):
    return round(a / b, 3) if b else 0.0


def totals(entries, conds):
    """Sum metrics over `entries` for each condition in `conds`."""
    out = {c: {"cost": 0.0, "total_in": 0, "cache_read": 0, "out": 0, "turns": 0} for c in conds}
    for e in entries:
        for c in conds:
            for k in out[c]:
                out[c][k] += e[c][k]
    return out


def fmt_usd(x):
    return f"${x:.4f}"


def grid_table(title, value_of):
    """2x2 markdown grid: rows = query format, cols = result format."""
    lines = [f"**{title}**", "",
             "| query \\ result | columnarjson | rowrepjson |",
             "|---|---|---|"]
    for qf, label in (("arrayjson", "arrayjson"), ("objectjson", "objectjson")):
        cells = []
        for rf in ("columnarjson", "rowrepjson"):
            cond = next(c for c, (q, r) in CONDITIONS.items() if q == qf and r == rf)
            cells.append(value_of(cond))
        lines.append(f"| **{label}** | {cells[0]} | {cells[1]} |")
    lines.append("")
    return lines


def write_markdown(rows, complete_ids, path):
    L = []
    A = L.append
    A("# Live-Agent 2x2 Format Comparison")
    A("")
    A(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    A(f"**Design:** query encoding (arrayjson | objectjson) × result encoding "
      f"(columnarjson | rowrepjson), {len(QUESTIONS)} questions each via `claude -p`.  ")
    A(f"**Base:** {len(complete_ids)}/{len(QUESTIONS)} questions succeeded in all four "
      "conditions; all aggregates below use that common set.  ")
    A("**Tokens:** `total_in` = input + cache-read + cache-create, summed across models. "
      "Ratios > 1 ⇒ the named variant costs more.")
    A("")

    if not complete_ids:
        A("_No question completed in all four conditions yet — run is partial._")
        A("")
        _write_per_question(A, rows)
        with open(path, "w") as f:
            f.write("\n".join(L))
        return

    entries = [rows[i] for i in complete_ids]
    t = totals(entries, COND_KEYS)
    n = len(complete_ids)

    # ── 1. Condition grids ──
    A("## 1. The 2×2")
    A("")
    L += grid_table("Total input+cache (tokens)", lambda c: f"{t[c]['total_in']:,}")
    L += grid_table("Total cost (USD)", lambda c: fmt_usd(t[c]["cost"]))
    L += grid_table("Total output (tokens)", lambda c: f"{t[c]['out']:,}")
    L += grid_table("Mean turns", lambda c: f"{t[c]['turns'] / n:.2f}")

    # ── 2. Main effects (marginals) ──
    # query axis: objectjson vs arrayjson, summed over both result formats.
    arr = {k: t["arr-col"][k] + t["arr-row"][k] for k in t["arr-col"]}
    obj = {k: t["obj-col"][k] + t["obj-row"][k] for k in t["obj-col"]}
    # result axis: rowrepjson vs columnarjson, summed over both query formats.
    col = {k: t["arr-col"][k] + t["obj-col"][k] for k in t["arr-col"]}
    row = {k: t["arr-row"][k] + t["obj-row"][k] for k in t["arr-row"]}

    A("## 2. Main effects (marginal, each axis averaged over the other)")
    A("")
    A("### Query axis — objectjson vs arrayjson (input encoding)")
    A("")
    A("| Metric | arrayjson | objectjson | objectjson / arrayjson |")
    A("|---|---|---|---|")
    A(f"| Total input+cache | {arr['total_in']:,} | {obj['total_in']:,} | **{ratio(obj['total_in'], arr['total_in']):.2f}x** |")
    A(f"| Total cost | {fmt_usd(arr['cost'])} | {fmt_usd(obj['cost'])} | **{ratio(obj['cost'], arr['cost']):.2f}x** |")
    A(f"| Total output | {arr['out']:,} | {obj['out']:,} | {ratio(obj['out'], arr['out']):.2f}x |")
    A("")
    A("### Result axis — rowrepjson vs columnarjson (output layout)")
    A("")
    A("| Metric | columnarjson | rowrepjson | rowrepjson / columnarjson |")
    A("|---|---|---|---|")
    A(f"| Total input+cache | {col['total_in']:,} | {row['total_in']:,} | **{ratio(row['total_in'], col['total_in']):.2f}x** |")
    A(f"| Total cost | {fmt_usd(col['cost'])} | {fmt_usd(row['cost'])} | **{ratio(row['cost'], col['cost']):.2f}x** |")
    A(f"| Total output | {col['out']:,} | {row['out']:,} | {ratio(row['out'], col['out']):.2f}x |")
    A("")

    # ── 3. Per-shape (total input+cache; the output axis should grow with rows) ──
    A("## 3. Per-shape — total input+cache and axis ratios")
    A("")
    A("| Shape | n | arr-col | arr-row | obj-col | obj-row | row/col (out axis) | obj/arr (in axis) |")
    A("|---|---|---|---|---|---|---|---|")
    for shape in SHAPES:
        ids = [i for i in complete_ids if rows[i]["shape"] == shape]
        if not ids:
            continue
        st = totals([rows[i] for i in ids], COND_KEYS)
        ti = {c: st[c]["total_in"] for c in COND_KEYS}
        row_axis = ratio(ti["arr-row"] + ti["obj-row"], ti["arr-col"] + ti["obj-col"])
        in_axis = ratio(ti["obj-col"] + ti["obj-row"], ti["arr-col"] + ti["arr-row"])
        A(f"| {shape} | {len(ids)} | {ti['arr-col']:,} | {ti['arr-row']:,} | "
          f"{ti['obj-col']:,} | {ti['obj-row']:,} | **{row_axis:.2f}x** | {in_axis:.2f}x |")
    A("")

    _write_per_question(A, rows)

    with open(path, "w") as f:
        f.write("\n".join(L))


def _write_per_question(A, rows):
    A("## Per-question detail — total input+cache (tokens)")
    A("")
    A("`—` = no result file. Conditions: arr=arrayjson, obj=objectjson · col=columnarjson, row=rowrepjson.")
    A("")
    A("| QID | Shape | arr-col | arr-row | obj-col | obj-row |")
    A("|---|---|---|---|---|---|")
    for qid, e in rows.items():
        def cell(c):
            return f"{e[c]['total_in']:,}" if e[c] else "—"
        A(f"| {qid} | {e['shape']} | {cell('arr-col')} | {cell('arr-row')} | "
          f"{cell('obj-col')} | {cell('obj-row')} |")
    A("")


def main():
    rows = collect()
    if not any(e[c] for e in rows.values() for c in COND_KEYS):
        sys.exit("No result files found under results/<condition>/. Run ./run_format_eval.sh first.")

    complete_ids = [i for i, e in rows.items() if all(e[c] and e[c]["ok"] for c in COND_KEYS)]

    os.makedirs(OUT_DIR, exist_ok=True)
    summary = {
        "conditions": {c: list(CONDITIONS[c]) for c in COND_KEYS},
        "complete_ids": complete_ids,
        "per_question": rows,
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    write_markdown(rows, complete_ids, os.path.join(OUT_DIR, "comparison.md"))

    print(f"2x2 comparison written to {OUT_DIR}/comparison.md")
    print(f"  complete (all 4 conditions): {len(complete_ids)}/{len(QUESTIONS)}")
    if complete_ids:
        t = totals([rows[i] for i in complete_ids], COND_KEYS)
        arr_in = t["arr-col"]["total_in"] + t["arr-row"]["total_in"]
        obj_in = t["obj-col"]["total_in"] + t["obj-row"]["total_in"]
        col_in = t["arr-col"]["total_in"] + t["obj-col"]["total_in"]
        row_in = t["arr-row"]["total_in"] + t["obj-row"]["total_in"]
        print(f"  query  axis (objectjson/arrayjson)   total_in = {ratio(obj_in, arr_in):.2f}x")
        print(f"  result axis (rowrepjson/columnarjson) total_in = {ratio(row_in, col_in):.2f}x")


if __name__ == "__main__":
    main()
