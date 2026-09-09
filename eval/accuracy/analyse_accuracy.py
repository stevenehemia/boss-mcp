#!/usr/bin/env python3
"""
Analyse accuracy pilot results: accuracy per (task x format) with Wilson 95%
CIs, and paired McNemar tests between every pair of formats

Usage (from eval/accuracy/):
    ./analyse_accuracy.py                             # newest results file
    ./analyse_accuracy.py results/acc_result_A.jsonl  # one specific file
    ./analyse_accuracy.py results/acc_result_*.jsonl  # pool several
    ./analyse_accuracy.py --errors                 # + error-structure pass (needs BOSS)
    ./analyse_accuracy.py --report out.json        # also write a JSON report

--errors re-fetches each wrong trial's exact fixture table through boss_evaluate
and checks whether the error was STRUCTURAL -- the model found a real value
in the table and misattributed it -- or unattributable

  LOOKUP
    row-offset      answer matches the SAME column at a DIFFERENT row
    column-swap     answer matches a DIFFERENT column at the SAME row
    unattributable  matches nothing in the table
  EXTREMUM (answer is {date, value})
    value-right-date-wrong     correct max value, attributed to the wrong date
    self-consistent-wrong-row  (date, value) is some other REAL row, not the max
    value-wrong                value doesn't match the row it's attributed to
    malformed                  unparseable / missing fields
"""

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import tables
import tasks as tasklib
from score import num_match

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
# run_accuracy.py writes acc_result_*; pilot_* is the historical prefix
RESULT_GLOBS = ("acc_result_*.jsonl", "pilot_*.jsonl")

# "columnarjson" was the typed encoding then, the plain one now),
# so old files are remapped on load rather than read as-is.
# Detected by the presence of a name the server never emits.
LEGACY_MARKERS = {"rowrepjson", "plaincolumnarjson", "schematuplesjson"}
LEGACY_RENAMES = {
    "columnarjson": "typedcolumnarjson",   # meant TYPED columnar back then
    "rowrepjson": "arrayofobjectsjson",
    "plaincolumnarjson": "columnarjson",
    "schematuplesjson": "positionalrowsjson",
    "indexedcolumnarjson": "indexedcolumnarjson",
}

STRUCTURAL = {"row-offset", "column-swap",
              "value-right-date-wrong", "self-consistent-wrong-row"}


# == loading ==================================================================

def load(paths):
    """All records from `paths`, with legacy format names normalised."""
    recs = []
    for path in paths:
        rows = [json.loads(line) for line in open(path) if line.strip()]
        if not rows:
            continue
        legacy = LEGACY_MARKERS & {r["format"] for r in rows}
        if legacy:
            for r in rows:
                r["format"] = LEGACY_RENAMES.get(r["format"], r["format"])
        note = f" (legacy names remapped -- saw {', '.join(sorted(legacy))})" if legacy else ""
        print(f"  {Path(path).name}: {len(rows)} records{note}")
        # theta is a per-run flag, so it is a property of the file, not of the record.
        # Adaptive thinking emits no thinking text on many calls --
        file_theta = ("on" if any((r.get("thinking") or "").strip() or
                                  r.get("thinking_enabled") for r in rows)
                      else "off")
        for r in rows:
            r.setdefault("_file_theta", file_theta)
        recs += rows
    return recs


def theta_of(rec):
    """Adaptive thinking emits no text on many calls, so a per-record text test
    would read roughly 40% of a theta=on run as off; theta is a per-run flag,
    so the whole file decides."""
    if "thinking_enabled" in rec:
        return "on" if rec["thinking_enabled"] else "off"
    return rec["_file_theta"]


def question_of(rec):
    """Identity of the question asked"""
    return json.dumps([rec.get("window"), rec.get("target"), rec.get("task")],
                      sort_keys=True)


def apply_filters(recs, args):
    """--model / --thinking selection, plus the heterogeneity check"""
    if args.model:
        recs = [r for r in recs if r.get("model") == args.model]
    if args.thinking:
        recs = [r for r in recs if theta_of(r) == args.thinking]
    if not recs:
        return recs

    models = sorted({r.get("model") for r in recs})
    thetas = sorted({theta_of(r) for r in recs})
    if len(models) > 1 or len(thetas) > 1:
        print("\n!! POOL IS HETEROGENEOUS !!")
        if len(models) > 1:
            print(f"     models: {', '.join(models)}   (select one with --model)")
        if len(thetas) > 1:
            counts = {t: sum(1 for r in recs if theta_of(r) == t) for t in thetas}
            print(f"     theta:  {counts}   (select one with --thinking on|off)")
        print()
    return recs


def group_cells(recs):
    """Records per (task, format) cell, in first-seen order."""
    cells = defaultdict(list)
    for r in recs:
        cells[(r["task"], r["format"])].append(r)
    return cells


# == statistics ===============================================================

def wilson(k, n, z=1.959964):
    """Wilson score interval; returns (point estimate, lo, hi)."""
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, center - margin), min(1.0, center + margin)


def mcnemar_exact(a_only, b_only):
    """Exact two-sided McNemar p-value from the discordant pair counts."""
    d = a_only + b_only
    if d == 0:
        return 1.0
    k = min(a_only, b_only)
    return min(sum(math.comb(d, i) for i in range(k + 1)) * 2 / 2 ** d, 1.0)


# == error classification =====================================================

def classify_lookup(trial, table):
    answer = trial["answer"]
    target_col, target_row = trial["target"]["column"], trial["target"]["row"]
    col_idx = table.columns.index(target_col)

    for row_idx, row in enumerate(table.rows):              # same column, different row
        if row_idx != target_row and row[col_idx] is not None and num_match(answer, row[col_idx]):
            return "row-offset", row_idx - target_row

    for other_idx, other_col in enumerate(table.columns):   # same row, different column
        if other_col in ("date", target_col):
            continue
        value = table.rows[target_row][other_idx]
        if value is not None and num_match(answer, value):
            return "column-swap", other_col

    return "unattributable", None


def classify_extremum(trial, table):
    answer, expected = trial["answer"], trial["expected"]
    if not isinstance(answer, dict):
        return "malformed", None
    if answer.get("date") is None or answer.get("value") is None:
        return "malformed", None
    answer_date, answer_value = str(answer["date"]).strip(), answer["value"]

    date_idx = table.columns.index("date")
    if num_match(answer_value, expected["value"]):
        # right value, wrong date -> pure row misattribution
        dates = [row[date_idx] for row in table.rows]
        try:
            offset = dates.index(answer_date) - trial["target"]["row"]
        except ValueError:
            offset = None
        return "value-right-date-wrong", offset

    col_idx = table.columns.index(trial["target"]["column"])
    for row in table.rows:
        if row[date_idx] == answer_date and num_match(answer_value, row[col_idx]):
            return "self-consistent-wrong-row", None
    return "value-wrong", None


def classify_errors(recs):
    """Re-fetch each wrong trial's fixture and classify the failure.
    boss_client comes via tables (whose import already set up the lib path)."""
    bc = tables.bc

    wrong = [r for r in recs if not r["correct"] and r["task"] in ("lookup", "extremum")]
    if not wrong:
        print("\nNo wrong lookup/extremum trials to classify.")
        return []

    print(f"\nClassifying {len(wrong)} wrong lookup/extremum trials...")
    fixtures, classified = {}, []
    with bc.session(result_format=tables.GROUND_TRUTH_FORMAT) as (proc, ids):
        for trial in wrong:
            window = trial["window"]
            key = (window["code"], window["start"], window["end"])
            if key not in fixtures:
                fixtures[key] = bc.parse_columnar(
                    bc.evaluate(proc, ids, tables.fixture_query(*key)))
            classify = classify_lookup if trial["task"] == "lookup" else classify_extremum
            category, detail = classify(trial, fixtures[key])
            classified.append({"trial": trial["trial"], "task": trial["task"],
                               "format": trial["format"], "category": category, "detail": detail})
    print(f"  ({len(fixtures)} unique fixture tables re-fetched)")
    return classified


# == reporting ================================================================

def accuracy_table(cells, tasks, formats):
    """Accuracy + CI + malformed rate per (task, format). Returns the cells."""
    stats = {key: {"correct": sum(bool(r["correct"]) for r in records), "n": len(records),
                   "malformed": sum(bool(r.get("malformed_first_try")) for r in records)}
             for key, records in cells.items()}

    print(f"\n{'task':<10} {'format':<20} {'n':>4} {'acc':>6}  {'95% CI':<16} {'malformed':>10}")
    print("-" * 74)
    for task in tasks:
        for fmt in formats:
            cell = stats.get((task, fmt))
            if not cell:
                continue
            acc, lo, hi = wilson(cell["correct"], cell["n"])
            print(f"{task:<10} {fmt:<20} {cell['n']:>4} {acc:>5.0%}  "
                  f"[{lo:>4.0%}, {hi:>4.0%}]  {cell['malformed']:>4}/{cell['n']:<5}")
        print()
    return {f"{task}|{fmt}": cell for (task, fmt), cell in stats.items()}


def cost_table(cells, tasks, formats):
    """Token cost per (task, format), and cost per correct answer"""
    print(f"\n{'task':<10} {'format':<20} {'n':>4} {'in':>8} {'out':>7} "
          f"{'total':>8} {'acc':>5} {'tok/correct':>12} {'vs best':>8}")
    print("-" * 88)
    out = {}
    for task in tasks:
        per_format = {}
        for fmt in formats:
            records = cells.get((task, fmt))
            if not records:
                continue
            usage = [r.get("usage") or {} for r in records]
            mean_in = statistics.mean(u.get("in", 0) for u in usage)
            mean_out = statistics.mean(u.get("out", 0) for u in usage)
            acc = sum(bool(r["correct"]) for r in records) / len(records)
            per_format[fmt] = {"n": len(records), "mean_in": mean_in, "mean_out": mean_out,
                               "accuracy": acc,
                               "tokens_per_correct": (mean_in + mean_out) / acc if acc else float("inf")}
        best = min((cell["tokens_per_correct"] for cell in per_format.values()), default=float("inf"))
        for fmt, cell in per_format.items():
            tokens_per_correct = cell["tokens_per_correct"]
            ratio = ("-" if tokens_per_correct == float("inf") or not best
                     else f"{tokens_per_correct / best:.2f}x")
            shown = "inf" if tokens_per_correct == float("inf") else f"{tokens_per_correct:,.0f}"
            print(f"{task:<10} {fmt:<20} {cell['n']:>4} {cell['mean_in']:>8,.0f} {cell['mean_out']:>7,.0f} "
                  f"{cell['mean_in'] + cell['mean_out']:>8,.0f} {cell['accuracy']:>4.0%} {shown:>12} {ratio:>8}")
            out[f"{task}|{fmt}"] = cell
        print()
    return out


def mcnemar_all(recs, tasks, formats):
    """Paired McNemar for every format pair, per task. Pairs on the QUESTION
    (task + question identity), not the per-file trial index. Since trial indices
    restart at 0 in every file, pairing on them would collide across pooled
    files. Harmless for same-seed repeats (same question), but silently
    mispairing/dropping records when pooling different seeds"""
    by = defaultdict(dict)
    for r in recs:
        by[r["format"]][(r["task"], question_of(r))] = bool(r["correct"])

    rows = []  # (task, a, b, both_right, both_wrong, a_only, b_only, p)
    for task in tasks:
        for i, a in enumerate(formats):
            for b in formats[i + 1:]:
                keys = [k for k in by[a] if k[0] == task and k in by[b]]
                if not keys:
                    continue
                a_only = sum(1 for k in keys if by[a][k] and not by[b][k])
                b_only = sum(1 for k in keys if by[b][k] and not by[a][k])
                both_r = sum(1 for k in keys if by[a][k] and by[b][k])
                rows.append((task, a, b, both_r, len(keys) - both_r - a_only - b_only,
                             a_only, b_only, mcnemar_exact(a_only, b_only)))
    if not rows:
        return {}

    print(f"\nMcNemar (paired on the same question; p from the exact test)\n"
          f"{'task':<10} {'A vs B':<45} {'both':>5} {'A+':>4} {'B+':>4} {'p':>8}")
    print("-" * 80)
    out = {}
    for task in tasks:
        for _, a, b, both_r, both_w, a_only, b_only, p in (r for r in rows if r[0] == task):
            print(f"{task:<10} {a + ' vs ' + b:<45} {both_r:>5} {a_only:>4} {b_only:>4} "
                  f"{p:>8.4f}{' *' if p < 0.05 else ''}")
            out[f"{task}|{a}_vs_{b}"] = {
                "both_right": both_r, "both_wrong": both_w,
                f"{a}_only": a_only, f"{b}_only": b_only, "p": p}
        print()
    return out


def aggregate_error_magnitude(recs):
    """For AGGREGATE, how wrong were the wrong answers? A near-miss computation
    slip and a wild misread both score 0, but mean very different things."""
    rows = [r for r in recs if r["task"] == "aggregate" and not r["correct"]]
    if not rows:
        return {}
    out = defaultdict(list)
    for r in rows:
        try:
            got, exp = float(r["answer"]), float(r["expected"])
        except (TypeError, ValueError):
            continue
        out[r["format"]].append(abs(got - exp) / max(abs(exp), 1e-9))
    if not out:
        return {}
    print(f"\nAGGREGATE wrong-answer magnitude (relative error)\n"
          f"{'format':<20} {'n':>3} {'median':>8} {'max':>8}")
    print("-" * 42)
    summary = {}
    for fmt, errs in out.items():
        med, worst = statistics.median(errs), max(errs)
        print(f"{fmt:<20} {len(errs):>3} {med:>8.1%} {worst:>8.1%}")
        summary[fmt] = {"n": len(errs), "median_rel_err": med, "max_rel_err": worst}
    return summary


def error_summary(classified):
    by_fmt = defaultdict(lambda: defaultdict(int))
    for entry in classified:
        by_fmt[entry["format"]][entry["category"]] += 1

    cats = sorted({entry["category"] for entry in classified})
    width = max(len(cat) for cat in cats) + 2
    print(f"\n{'category':<{width}}" + "".join(f"{f:>22}" for f in by_fmt))
    print("-" * (width + 22 * len(by_fmt)))
    for cat in cats:
        print(f"{cat:<{width}}" + "".join(f"{by_fmt[f][cat]:>22}" for f in by_fmt))
    share = {f: sum(v for k, v in by_fmt[f].items() if k in STRUCTURAL)
                / max(sum(by_fmt[f].values()), 1)
             for f in by_fmt}
    print(f"\n{'STRUCTURAL share':<{width}}" + "".join(f"{share[f]:>21.0%} " for f in by_fmt))
    print("*structural = the model found a real value and bound it to the wrong row/column")
    return {f: dict(v) for f, v in by_fmt.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("files", nargs="*", help="pilot JSONL files (default: newest in results/)")
    p.add_argument("--errors", action="store_true",
                   help="classify every wrong answer (re-fetches fixtures via BOSS)")
    p.add_argument("--report", help="write a JSON report to this path")
    p.add_argument("--model", help="analyse only for this model (e.g. claude-sonnet-5)")
    p.add_argument("--thinking", choices=("on", "off"),
                   help="analyse results only with thinking on or off")
    args = p.parse_args()

    paths = [Path(f) if Path(f).exists() else RESULTS_DIR / f for f in args.files]
    if not paths:
        found = sorted((q for pat in RESULT_GLOBS for q in RESULTS_DIR.glob(pat)),
                       key=lambda q: q.stat().st_mtime)
        if not found:
            sys.exit(f"no results/{{{','.join(RESULT_GLOBS)}}} found -- "
                     "run ./run_accuracy.py first")
        paths = [found[-1]]
        print(f"(no file given; using the most recent: {paths[0].name})")

    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit(f"no such file: {', '.join(str(m) for m in missing)}")

    print("Loading:")
    recs = load(paths)
    if not recs:
        sys.exit("no records found")

    recs = apply_filters(recs, args)
    if not recs:
        sys.exit("no records left after --model/--thinking filtering")

    models = sorted({r["model"] for r in recs})
    nrows = sorted({r["nrows"] for r in recs})
    tasks = [t for t in tasklib.TASKS if t in {r["task"] for r in recs}]
    formats = [f for f in tables.FORMATS if f in {r["format"] for r in recs}]
    formats += sorted({r["format"] for r in recs} - set(formats))
    cells = group_cells(recs)

    print(f"\n{len(recs)} calls | models: {', '.join(models)} | "
          f"table rows: {min(nrows)}-{max(nrows)}")

    report = {"files": [str(p) for p in paths], "models": models,
              "tasks": tasks, "formats": formats}
    report["cells"] = accuracy_table(cells, tasks, formats)
    report["cost"] = cost_table(cells, tasks, formats)
    report["mcnemar"] = mcnemar_all(recs, tasks, formats)
    agg = aggregate_error_magnitude(recs)
    if agg:
        report["aggregate_error_magnitude"] = agg

    if args.errors:
        classified = classify_errors(recs)
        if classified:
            report["error_structure"] = error_summary(classified)
            report["error_trials"] = classified

    if args.report:
        out = Path(args.report)
        out.write_text(json.dumps(report, indent=2))
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
