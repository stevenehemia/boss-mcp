#!/usr/bin/env python3
"""
Ground truth for the efficiency evaluation scoring

Graded facts
---------------------------------
  days_covered      how many daily rows the answer is based on. Test for
                    "did the agent correctly retrieve the window"
  peak_cases_date   
  peak_cases_value  
  peak_hosp_date    

Usage (from eval/efficiency/):
    ../.venv/bin/python3 generate_ground_truth.py            # print
    ../.venv/bin/python3 generate_ground_truth.py --write    # -> ground_truth.json
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))
sys.path.insert(0, _HERE)

import boss_client as bc
import importlib 

_q = importlib.import_module(os.environ.get("QUESTIONS_MODULE", "questions"))
DATA_PATH, METRICS, SHAPES, WINDOWS = _q.DATA_PATH, _q.METRICS, _q.SHAPES, _q.WINDOWS

OUT_PATH = os.path.join(_HERE, os.environ.get("GROUND_TRUTH", "ground_truth.json"))

PEAK_COLUMNS = {
    "cases": "new_cases_smoothed_per_million",
    "hosp": "hosp_patients_per_million",
}

PEAK_REL_TOL = 0.005


def query_expr(code, start, end):
    return ["Project",
            ["Filter",
             ["Filter", ["Load", ["String", DATA_PATH]],
              ["Equal", ["Symbol", "code"], ["String", code]]],
             ["And", ["Greater", ["Symbol", "date"], ["String", start]],
                     ["Less", ["Symbol", "date"], ["String", end]]]],
            ["Symbol", "date"], *[["Symbol", c] for c in METRICS]]


def _argmax_dates(table, column):
    """(max value, [every date within PEAK_REL_TOL of it]).
    Ensuring other dates within tolerance are still correct"""
    idx = {name: j for j, name in enumerate(table.columns)}
    ci, di = idx[column], idx["date"]
    pairs = [(r[ci], r[di]) for r in table.rows if r[ci] is not None]
    if not pairs:
        return None, []
    peak = max(v for v, _ in pairs)
    cutoff = peak - PEAK_REL_TOL * abs(peak)
    return peak, sorted(d for v, d in pairs if v >= cutoff)


def compute():
    truth = {}
    with bc.session(result_format="typedcolumnarjson") as (proc, ids):
        for shape in SHAPES:
            code, start, end = WINDOWS[shape]
            table = bc.parse_columnar(bc.evaluate(proc, ids, query_expr(code, start, end)))
            entry = {"country": code, "window": [start, end], "days_covered": table.nrows}
            for label, column in PEAK_COLUMNS.items():
                peak, dates = _argmax_dates(table, column)
                entry[f"peak_{label}_value"] = peak
                entry[f"peak_{label}_dates"] = dates
            truth[shape] = entry
    return truth


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--write", action="store_true", help=f"write {OUT_PATH}")
    args = p.parse_args()

    truth = compute()
    print(f"{'tier':6s} {'days':>5s}  {'peak cases date(s)':<34s} {'peak cases':>11s}  peak hosp date(s)")
    print("-" * 100)
    for shape in SHAPES:
        t = truth[shape]
        cd = ", ".join(t["peak_cases_dates"])
        hd = ", ".join(t["peak_hosp_dates"])
        tie = "  (plateau)" if len(t["peak_cases_dates"]) > 1 else ""
        print(f"{shape:6s} {t['days_covered']:>5d}  {cd:<34s} {t['peak_cases_value']:>11,.2f}  {hd}{tie}")

    if args.write:
        with open(OUT_PATH, "w") as f:
            json.dump(truth, f, indent=2)
        print(f"\nWrote {OUT_PATH}")
    else:
        print("\n(pass --write to save ground_truth.json)")


if __name__ == "__main__":
    main()
