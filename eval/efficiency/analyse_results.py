#!/usr/bin/env python3
"""
Score and analyse the efficiency evaluation

Phase 1 — scoring: each run's trailing JSON object is graded against the
ground-truth file. An unparseable answer scores wrong and is flagged
malformed. Writes results/scores.json.
  valid    the answer is based on the whole window (`days_covered` matches).
  correct  every graded claim matches — peak dates and the peak value.

Phase 2 — analysis: Reports per-tier total_in / turns / denials as mean ±SD
across replicates
"""

import argparse
import glob
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

import importlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_q = importlib.import_module(os.environ.get("QUESTIONS_MODULE", "questions"))
CAP_TOKENS, CONDITIONS, QUESTIONS = _q.CAP_TOKENS, _q.CONDITIONS, _q.QUESTIONS
RESULT_TOKENS, SHAPES, over_cap = (_q.RESULT_TOKENS, _q.SHAPES, _q.over_cap)

REQUIRED_FIELDS = ("days_covered", "peak_cases_date",
                   "peak_cases_value", "peak_hosp_date")

REPLICATES = int(os.environ.get("REPLICATES", _q.REPLICATES))

RESULTS_DIR = os.path.join(_HERE, os.environ.get("RESULTS_DIR", "results"))
TRUTH_PATH = os.path.join(_HERE, os.environ.get("GROUND_TRUTH", "ground_truth.json"))

COND_KEYS = list(CONDITIONS)
METRIC_KEYS = ("cost", "total_in", "cache_read", "out", "turns", "denials")
REL_TOL = 0.005

# days_covered tolerance to accommodate phrases like "mid-January to late June"
DAYS_ABS_TOL = 5
DAYS_REL_TOL = 0.05


def _parse_stream(text, path):
    """Last `{"type": "result"}` line of a transcript"""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # A line might be truncated,
            # keep scanning back for the last intact message.
            continue
        if isinstance(msg, dict) and msg.get("type") == "result":
            return msg
    raise ValueError(f"{path}: stream transcript has no result message.")


def load_run(path):
    """-> the run's summary object, from either format the runner can write:
    the verbose stream (`--output-format stream-json --verbose`, one JSON
    message per line) or the compact object (`--output-format json`).

    Raises rather than returning a partial: a dead run accepted as empty would
    score as a free, fast, wrong one and drag its format's mean cost down."""
    with open(path) as f:
        text = f.read()
    if not text.strip():
        raise ValueError(f"{path}: empty file — the cell is still running, or died.")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return _parse_stream(text, path)
    # A compact file is one object; a single-line stream would also parse here,
    # so only accept it as compact if it isn't a lone stream message.
    if isinstance(obj, dict) and obj.get("type") in (None, "result"):
        return obj
    raise ValueError(f"{path}: unrecognised run file (type={obj.get('type')!r}).")


def replicate_paths(cond, qid):
    paths = glob.glob(os.path.join(RESULTS_DIR, cond, f"{qid}_rep*.json"))
    return sorted(paths, key=lambda p: int(os.path.basename(p).rsplit("_rep", 1)[1].split(".")[0]))


# == Stage 1: scoring ===========================================================

def parse_tail(text):
    """Extract the trailing JSON object"""
    if not text:
        return None
    for m in reversed(list(re.finditer(r"\{[^{}]*\}", text, re.DOTALL))):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(k in obj for k in REQUIRED_FIELDS):
            return obj
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _days_ok(got, truth):
    v = _num(got)
    exp = truth["days_covered"]
    return v is not None and abs(v - exp) <= max(DAYS_ABS_TOL, DAYS_REL_TOL * exp)


def _in_date_set(key):
    def rule(got, truth):
        return str(got if got is not None else "").strip() in truth[key]
    return rule


def _near(key):
    def rule(got, truth):
        v = _num(got); exp = truth[key]
        return (v is not None and exp is not None
                and abs(v - exp) <= REL_TOL * max(abs(exp), 1e-9))
    return rule


FIELD_RULES = {
    "days_covered":     _days_ok,
    "peak_cases_date":  _in_date_set("peak_cases_dates"),
    "peak_cases_value": _near("peak_cases_value"),
    "peak_hosp_date":   _in_date_set("peak_hosp_dates"),
}

def score_one(answer, truth):
    """-> (valid, correct, per-field dict).
    `answer` may be None (malformed)."""
    if answer is None:
        return False, False, {k: None for k in REQUIRED_FIELDS}

    fields = {k: FIELD_RULES[k](answer.get(k), truth) for k in REQUIRED_FIELDS}
    # days_covered acts as the validity gate, testing whether the answer
    # was based on the correct window
    return fields["days_covered"], all(fields.values()), fields


def score_runs(truth, verbose):
    """Grade every run on disk, print the scoring tables, write scores.json"""
    runs = []
    for q in QUESTIONS:
        for cond in CONDITIONS:
            for path in replicate_paths(cond, q["id"]):
                d = load_run(path)
                answer = parse_tail(d.get("result", ""))
                valid, correct, fields = score_one(answer, truth[q["shape"]])
                runs.append({
                    "tier": q["id"], "shape": q["shape"], "condition": cond,
                    "path": os.path.relpath(path, _HERE),
                    "malformed": answer is None,
                    "valid": valid, "correct": correct, "fields": fields,
                    "answer": answer,
                })

    if not runs:
        print("No result files found - run ./run_efficiency_eval.sh first.")
        return None

    if verbose:
        for r in runs:
            flags = "".join(("M" if r["malformed"] else "-",
                             "V" if r["valid"] else "-",
                             "C" if r["correct"] else "-"))
            bad = [k for k, v in r["fields"].items() if v is False]
            print(f"  [{flags}] {r['tier']:5s} {r['condition']:11s} {r['path']}"
                  f"{'  failed: ' + ','.join(bad) if bad else ''}")
        print()

    print(f"{'condition':12s} {'n':>4s} {'malformed':>10s} {'valid':>8s} {'correct':>9s} "
          f"{'correct|valid':>14s}")
    print("-" * 62)
    summary = {}
    for cond in CONDITIONS:
        rs = [r for r in runs if r["condition"] == cond]
        if not rs:
            continue
        n = len(rs)
        mal = sum(r["malformed"] for r in rs)
        val = sum(r["valid"] for r in rs)
        cor = sum(r["correct"] for r in rs)   # correct implies valid (see score_one)
        print(f"{cond:12s} {n:>4d} {mal:>4d} ({mal/n:>3.0%}) {val:>3d} ({val/n:>3.0%}) "
              f"{cor:>3d} ({cor/n:>3.0%}) {cor:>7d}/{val:<3d}")
        summary[cond] = {"n": n, "malformed": mal, "valid": val, "correct": cor}

    print(f"\n{'tier':6s} " + " ".join(f"{c:>11s}" for c in CONDITIONS) + "   (valid/n)")
    for shape in SHAPES:
        qid = next(q["id"] for q in QUESTIONS if q["shape"] == shape)
        cells = []
        for cond in CONDITIONS:
            rs = [r for r in runs if r["tier"] == qid and r["condition"] == cond]
            cells.append(f"{sum(r['valid'] for r in rs)}/{len(rs)}" if rs else "-")
        print(f"{qid:6s} " + " ".join(f"{c:>11s}" for c in cells))

    out = os.path.join(RESULTS_DIR, "scores.json")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(out, "w") as f:
        json.dump({"summary": summary, "runs": runs}, f, indent=2)
    print(f"\nWrote {out}\n")
    return {r["path"]: r["valid"] for r in runs}


# == Stage 2: analysis ==========================================================

def extract(path):
    """Comparable metrics out of one `claude -p --output-format json` file"""
    d = load_run(path)
    mu = d.get("modelUsage", {}) or {}

    def s(field):
        return sum(m.get(field, 0) for m in mu.values())

    in_raw = s("inputTokens")
    cr = s("cacheReadInputTokens")
    cc = s("cacheCreationInputTokens")
    denials = d.get("permission_denials", []) or []
    return {
        "ok": not d.get("is_error", False),
        "turns": d.get("num_turns", 0),
        "cost": d.get("total_cost_usd", 0.0),
        "cache_read": cr,
        "out": s("outputTokens"),
        "total_in": in_raw + cr + cc,
        "denials": len(denials),
        "denied_tools": sorted({x.get("tool_name", "?") for x in denials}),
    }


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def cell_stats(paths, validity):
    """Mean/SD over usable replicates"""
    reps = []
    n_invalid = 0
    for p in paths:
        r = extract(p)
        if not r["ok"]:
            continue
        if validity is not None and not validity.get(os.path.relpath(p, _HERE), True):
            n_invalid += 1
            continue
        reps.append(r)
    out = {"n_ok": len(reps), "n_total": len(paths), "n_invalid": n_invalid}
    for k in METRIC_KEYS:
        vals = [r[k] for r in reps]
        out[k] = {"mean": mean(vals), "sd": stdev(vals), "values": vals}
    # Union across replicates — which tools the agent reached for when blocked.
    out["denied_tools"] = sorted({t for r in reps for t in r["denied_tools"]})
    out["n_reps_with_denials"] = sum(1 for r in reps if r["denials"])
    return out


def collect(validity):
    """{id: {shape, <cond>: cell_stats|None}}. None means no data on disk."""
    rows = {}
    for q in QUESTIONS:
        entry = {"shape": q["shape"]}
        for cond in COND_KEYS:
            paths = replicate_paths(cond, q["id"]) if cond in q["conditions"] else []
            entry[cond] = cell_stats(paths, validity) if paths else None
        rows[q["id"]] = entry
    return rows


def write_markdown(rows, path, validity):
    A = []
    add = A.append
    add("# Data representation format efficiency — live agent")
    add("")
    add(f"Generated: {datetime.now(timezone.utc).isoformat()}  ")
    add(f"Replicates per cell: {REPLICATES}  ")
    add(f"Output cap: {CAP_TOKENS:,} tokens  ")
    add("")
    if validity is None:
        add("> **Not filtered for correctness.** No scoring verdicts exist for this tree")
    else:
        n_dropped = sum(rows[q["id"]][c]["n_invalid"]
                        for q in QUESTIONS for c in COND_KEYS if rows[q["id"]][c])
        add(f"Filtered for correctness. Metrics below are over valid runs only."
            f"{n_dropped} run(s) excluded as invalid.")
    add("")

    add("## 1. Per-tier detail (mean ±SD across replicates)")
    add("")
    add("`*` marks a cell predicted to breach the output cap. `n/a` = no data yet.")
    add("")
    add("| Tier | Metric | " + " | ".join(f"`{c}`" for c in COND_KEYS) + " |")
    add("|---|---|" + "---|" * len(COND_KEYS))
    for q in QUESTIONS:
        qid, shape = q["id"], q["shape"]
        for k, label, f in (("total_in", "total_in", "{:,.0f}"),
                            ("turns", "turns", "{:.1f}"),
                            ("denials", "denials", "{:.1f}")):
            cells = []
            for c in COND_KEYS:
                st = rows[qid][c]
                mark = "*" if over_cap(shape, c) else ""
                if not st or not st["n_ok"]:
                    cells.append(f"n/a{mark}")
                else:
                    cells.append(f"{f.format(st[k]['mean'])} ±{f.format(st[k]['sd'])}{mark}")
            head = f"**{qid}** ({shape})" if k == "total_in" else ""
            add(f"| {head} | {label} | " + " | ".join(cells) + " |")
    add("")

    add("### Blocked tool attempts")
    add("")
    flagged = [(q["id"], c, rows[q["id"]][c])
               for q in QUESTIONS for c in COND_KEYS
               if rows[q["id"]][c] and rows[q["id"]][c]["n_ok"]
               and rows[q["id"]][c]["denials"]["mean"] > 0]
    if not flagged:
        add("_None — no run had a tool call blocked._")
    else:
        add("| Tier | Format | reps w/ denials | denials mean ±SD | tools attempted |")
        add("|---|---|---|---|---|")
        for qid, cond, st in flagged:
            add(f"| {qid} | `{cond}` | {st['n_reps_with_denials']}/{st['n_ok']} | "
                f"{st['denials']['mean']:.1f} ±{st['denials']['sd']:.1f} | "
                f"{', '.join('`' + t + '`' for t in st['denied_tools'])} |")
    add("")

    with open(path, "w") as f:
        f.write("\n".join(A) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--verbose", action="store_true", help="per-run scoring detail")
    args = ap.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)

    # == Stage 1: scoring =======================================================
    validity = None
    if os.path.exists(TRUTH_PATH):
        with open(TRUTH_PATH) as f:
            truth = json.load(f)
        print("Scoring responses...")
        validity = score_runs(truth, args.verbose)
    else:
        gt = os.environ.get("GROUND_TRUTH", "ground_truth.json")
        qm = os.environ.get("QUESTIONS_MODULE", "questions")
        print(f"NOTE: {TRUTH_PATH} absent — metrics are UNFILTERED for correctness.")
        print(f"      Generate it with: QUESTIONS_MODULE={qm} GROUND_TRUTH={gt} "
              f"../.venv/bin/python3 generate_ground_truth.py --write\n")

    # == Phase 2: analysis ======================================================
    rows = collect(validity)

    n_target = len(QUESTIONS) * len(COND_KEYS)
    n_have = sum(1 for q in QUESTIONS for c in COND_KEYS
                 if rows[q["id"]][c] and rows[q["id"]][c]["n_ok"])

    summary = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "replicates": REPLICATES,
        "cap_tokens": CAP_TOKENS,
        "conditions": CONDITIONS,
        "result_tokens": RESULT_TOKENS,
        "cells_with_data": n_have,
        "cells_total": n_target,
        "filtered_for_correctness": validity is not None,
        "rows": rows,
    }
    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    write_markdown(rows, os.path.join(RESULTS_DIR, "comparison.md"), validity)

    print(f"cells with usable data: {n_have}/{n_target}")
    for shape in SHAPES:
        qid = next(q["id"] for q in QUESTIONS if q["shape"] == shape)
        have, absent, dropped = [], [], []
        for c in COND_KEYS:
            st = rows[qid][c]
            if st is None:
                absent.append(c)          # never ran
            elif st["n_ok"]:
                have.append(c)
            else:
                dropped.append(c)         # no usable cells
        note = ""
        if absent:
            note += "  not run: " + ",".join(absent)
        if dropped:
            note += "  all invalid: " + ",".join(dropped)
        print(f"  {qid:5s} ({shape:4s}): {len(have)}/{len(COND_KEYS)} formats{note}")
    print(f"\nWrote {RESULTS_DIR}/comparison.md and {RESULTS_DIR}/summary.json")


if __name__ == "__main__":
    main()
