#!/usr/bin/env python3
"""
Token cost of every result format, per tokenizer, with no agent in the loop.

Query sets:
  shapes  8 queries: 4 response shapes x short/long column names
  tiers   eval/efficiency's size ladder (~60-600 rows, ~5k-183k chars)
  grid    32 queries: row count stepped ~geometrically 5..500 x (5|10 cols)
          x short/long column names, all prefixes of one dense ITA window

  shape        query
  scalar       global mean of 1 metric                      (1 row x 1 col)
  tall_narrow  per-country mean of 4 metrics (+ code)       (248 rows x 5 col)
  short_wide   first 5 raw rows, 9 metrics (+ code)         (5 rows x 10 col)
  tall_wide    per-country mean of 9 metrics (+ code)       (248 rows x 10)

Requirements:
    pip install tiktoken anthropic          (eval/.venv has both)

Usage (from eval/token_cost/):
    ./measure.py   # shapes, tiktoken only
    ./measure.py --claude-models claude-sonnet-5 claude-haiku-4-5
    ./measure.py --queries tiers --claude-models claude-sonnet-5
    ./measure.py --queries grid
    ./measure.py --tiktoken-encodings gpt-4

Output (tag names the query set and the tokenizers measured):
    results/trials_<queries>_<tokenizers>.json
    results/report_<queries>_<tokenizers>.txt
"""

import argparse
import json
import os
import sys
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from functools import partial

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))

from utils import (DATA_PATH, count_tokens_tiktoken, count_tokens_claude,
                   save_json, write_lines, boss_call, boss_session)

RESULTS_DIR = os.path.join(_HERE, "results")

FORMATS = {
    "typed_columnar":   "typedcolumnarjson",
    "plain_columnar":   "columnarjson",
    "indexed_columnar": "indexedcolumnarjson",
    "positional_rows":  "positionalrowsjson",
    "array_of_objects": "arrayofobjectsjson",
}
RATIO_BASE = "array_of_objects"

DEFAULT_TIKTOKEN_ENCODINGS = ["gpt-4", "gpt-5"]

SHORT_METRICS = [
    "new_cases", "new_deaths", "excess_mortality", "hosp_patients",
    "icu_patients", "new_tests", "new_vaccinations", "people_vaccinated",
    "total_boosters",
]

LONG_METRICS = [
    "new_cases_smoothed_per_million",
    "new_deaths_smoothed_per_million",
    "excess_mortality_cumulative_per_million",
    "weekly_hosp_admissions_per_million",
    "weekly_icu_admissions_per_million",
    "new_tests_smoothed_per_thousand",
    "new_vaccinations_smoothed_per_million",
    "new_people_vaccinated_smoothed_per_hundred",
    "total_boosters_per_hundred",
]

GRID_ROWS = (5, 10, 20, 50, 100, 200, 350, 500)

GRID_CODE, GRID_START = "ITA", "2020-12-17"

GRID_SHORT_METRICS = [
    "new_cases", "new_deaths", "total_cases", "total_deaths",
    "hosp_patients", "icu_patients", "new_tests", "total_tests",
    "positive_rate",
]

GRID_LONG_METRICS = [
    "new_cases_per_million",
    "new_deaths_per_million",
    "new_cases_smoothed_per_million",
    "new_deaths_smoothed_per_million",
    "total_cases_per_million",
    "total_deaths_per_million",
    "hosp_patients_per_million",
    "icu_patients_per_million",
    "total_tests_per_thousand",
]


def _groupby_rename(metrics, keys=()):
    """Construct a BOSS expression to compute the mean of the requested
    metrics, grouped by the optional key columns, then rename the
    auto-generated mean() columns back to the bare metric names."""
    inner = ["GroupBy",
                ["Load",
                    ["String", DATA_PATH]
                ],
                *[["Mean", ["Symbol", m]] for m in metrics],
                *[["Symbol", k] for k in keys]
            ]
    outer_args = [["Symbol", k] for k in keys] + [
        ["As", ["Symbol", f"mean({m})"], ["Symbol", m]] for m in metrics
    ]
    return ["Project", inner, *outer_args]


def _slice_select(n, metrics, keys=("code",)):
    """Construct a BOSS expression that evaluates to the first n rows of the
    dataset, selecting the key columns and the requested metrics."""
    cols = [["Symbol", k] for k in keys] + [["Symbol", m] for m in metrics]
    return ["Project",
               ["Slice",
                   ["Load", ["String", DATA_PATH]], ["Int", 0], ["Int", n]
               ],
               *cols
           ]


def _shape_queries():
    queries = []
    for length, metrics in (("short", SHORT_METRICS), ("long", LONG_METRICS)):
        for tag, shape, description, expr in (
            ("SC", "scalar",      "Global mean of 1 metric, {} column name",
             _groupby_rename(metrics[:1])),
            ("TN", "tall_narrow", "Per-country mean of 4 metrics, {} column names",
             _groupby_rename(metrics[:4], keys=("code",))),
            ("SW", "short_wide",  "First 5 raw rows, 9 metrics, {} column names",
             _slice_select(5, metrics, keys=("code",))),
            ("TW", "tall_wide",   "Per-country mean of 9 metrics, {} column names",
             _groupby_rename(metrics, keys=("code",))),
        ):
            queries.append({"id": f"{tag}-{length}", "shape": shape, "name_length": length,
                            "description": description.format(length), "boss_query": expr})
    return queries


def _window_select(code, start, n_rows, metrics):
    """First n_rows days of a dense per-country window: the tiers set's query
    shape (Filter on code and date, Project date + metrics), sized by the
    exclusive end date rather than Slice so the grid varies size through the
    same mechanism as the calibration corpus."""
    end = (date.fromisoformat(start) + timedelta(days=n_rows + 1)).isoformat()
    return ["Project",
               ["Filter",
                   ["Filter", ["Load", ["String", DATA_PATH]],
                       ["Equal", ["Symbol", "code"], ["String", code]]],
                   ["And", ["Greater", ["Symbol", "date"], ["String", start]],
                           ["Less", ["Symbol", "date"], ["String", end]]]],
               ["Symbol", "date"], *[["Symbol", m] for m in metrics]]


def _grid_queries():
    queries = []
    for length, metrics in (("short", GRID_SHORT_METRICS), ("long", GRID_LONG_METRICS)):
        for cols, ms in ((5, metrics[:4]), (10, metrics)):
            for n in GRID_ROWS:
                queries.append({
                    "id": f"{n}x{cols}-{length}", "shape": f"{n}x{cols}",
                    "name_length": length,
                    "description": f"First {n} days of the dense {GRID_CODE} "
                                   f"window, date + {len(ms)} metrics, "
                                   f"{length} column names",
                    "expect_rows": n,
                    "boss_query": _window_select(GRID_CODE, GRID_START, n, ms)})
    return queries


def _tier_queries():
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "efficiency"))
    from questions import METRICS, WINDOWS
    from measure_tiers import query_expr
    return [{"id": tier, "shape": tier, "name_length": "long",
             "description": f"{code} {start}..{end}, date + {len(METRICS)} metrics",
             "boss_query": query_expr(code, start, end)}
            for tier in ("xs", "s", "m", "l", "xl", "xxl", "xxxl")
            for code, start, end in [WINDOWS[tier]]]


QUERY_SETS = {"shapes": _shape_queries, "tiers": _tier_queries,
              "grid": _grid_queries}


def measure_query(q, sessions, tokenizers) -> dict:
    """Fetch q in every format and count each under every tokenizer.

    sessions:   {format label: (proc, ids)}, one open session per FORMATS entry
    tokenizers: {label: count(text) -> int}"""
    served = {}
    for label, (proc, ids) in sessions.items():
        resp = boss_call(proc, q["boss_query"], next(ids))
        if not resp["success"]:
            raise RuntimeError(f"{q['id']} failed in {label}: {resp['error']}")
        served[label] = resp["result"]

    # identify actual row and column counts from positional payload
    data = json.loads(served["positional_rows"])
    rows, cols = len(data) - 1, len(data[0]) - 1
    if q.get("expect_rows") is not None and rows != q["expect_rows"]:
        raise RuntimeError(f"{q['id']}: expected {q['expect_rows']} rows, got "
                           f"{rows} -- a gap in the window would bend the "
                           "grid's row-count axis, so refuse to measure it")

    counts = {}
    for tok_label, count in tokenizers.items():
        tokens = {label: count(text) for label, text in served.items()}
        counts[tok_label] = {
            label: {"chars": len(served[label]), "tokens": n,
                    "ratio": round(n / tokens[RATIO_BASE], 3)}
            for label, n in tokens.items()
        }
    return {"id": q["id"], "shape": q["shape"], "name_length": q["name_length"],
            "description": q["description"], "rows": rows, "cols": cols,
            "tokenizers": counts}


def write_report(trials, path):
    lines = [
        "Token cost per result format",
        "=" * 72,
        f"Generated  : {datetime.now(timezone.utc).isoformat()}",
        f"Tokenisers : {', '.join(trials[0]['tokenizers'])}",
        f"Queries    : {len(trials)}",
        f"Ratio      : tokens / tokens[{RATIO_BASE}]",
    ]
    for t in trials:
        lines += ["", "-" * 72,
                  f"{t['id']:12s} [{t['shape']}/{t['name_length']}]  {t['description']}",
                  f"  rows={t['rows']} cols={t['cols']}"]
        for tok_label, by_format in t["tokenizers"].items():
            lines.append(f"  [{tok_label}]")
            for fmt, c in by_format.items():
                lines.append(f"    {fmt:17s} chars={c['chars']:7d}  "
                             f"tokens={c['tokens']:6d}  ratio={c['ratio']:.3f}")
    write_lines(path, lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--queries", choices=QUERY_SETS, default="shapes",
                   help="query set to measure (default: shapes)")
    p.add_argument("--tiktoken-encodings", nargs="+", default=DEFAULT_TIKTOKEN_ENCODINGS,
                   metavar="ENC",
                   help="tiktoken model or encoding names "
                        f"(default: {' '.join(DEFAULT_TIKTOKEN_ENCODINGS)})")
    p.add_argument("--claude-models", nargs="+", default=[], metavar="MODEL",
                   help="Claude models to count via the count_tokens endpoint "
                        "(needs ANTHROPIC_API_KEY; default: none)")
    args = p.parse_args()

    tokenizers = {enc: partial(count_tokens_tiktoken, encoding=enc)
                  for enc in args.tiktoken_encodings}
    if args.claude_models:
        import anthropic
        client = anthropic.Anthropic()
        tokenizers.update({m: partial(count_tokens_claude, client, model=m)
                           for m in args.claude_models})

    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Token cost per result format")
    print(f"Tokenisers: {', '.join(tokenizers)}")
    print("=" * 72)

    trials = []
    with ExitStack() as stack:
        sessions = {label: stack.enter_context(boss_session(result_format=fmt))
                    for label, fmt in FORMATS.items()}
        for q in QUERY_SETS[args.queries]():
            print(f"{q['id']:12s} [{q['shape']}/{q['name_length']}] ...", end=" ", flush=True)
            result = measure_query(q, sessions, tokenizers)
            trials.append(result)
            print(f"rows={result['rows']} cols={result['cols']}")
            for tok_label, by_format in result["tokenizers"].items():
                ratios = "  ".join(f"{fmt}={c['ratio']:.2f}" for fmt, c in by_format.items())
                print(f"    {tok_label:10s} {ratios}")

    parts = [args.queries,
             "+".join(m.removeprefix("claude-") for m in args.claude_models),
             "+".join(args.tiktoken_encodings)]
    tag = "_" + "_".join(part for part in parts if part)
    save_json(f"{RESULTS_DIR}/trials{tag}.json", trials)
    write_report(trials, f"{RESULTS_DIR}/report{tag}.txt")
    print(f"\nwrote {RESULTS_DIR}/trials{tag}.json and report{tag}.txt")


if __name__ == "__main__":
    main()
