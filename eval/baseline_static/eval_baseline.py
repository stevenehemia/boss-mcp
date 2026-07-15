#!/usr/bin/env python3
"""
Column-Name-Length Token Efficiency Baseline
=============================================

Compares response token usage under short vs long real dataset column names.
Response shapes are categorised into four types, each with a short/long pair:

  scalar        1x1              global mean of 1 metric
  tall_narrow   248 rows x 5     per-country mean of 4 metrics (+ code)
  short_wide    5 rows x 10      raw slice, 9 metrics (+ code)
  tall_wide     248 rows x 10    per-country mean of 9 metrics (+ code)

Two shape pairs each hold one factor fixed so the other can be isolated,
both anchored on tall_wide:
  - tall_narrow vs tall_wide: identical row set (grouped by `code`, same 248
    countries/regions) -- fixed R=248, only column count (5 vs 10) and name
    length vary.
  - short_wide vs tall_wide: identical column set (code + all 9 metrics) --
    fixed C=10, only row count (5 vs 248) and name length vary.

Every response is measured under every requested tokeniser in a single pass:
one or more tiktoken encodings (--tiktoken-encodings, local/free) and one or
more Claude models (--claude-models, via the free `count_tokens` endpoint).

Requirements:
    pip install tiktoken anthropic

Auth:
    ANTHROPIC_API_KEY in the environment is needed for Claude token counts

Usage examples:
    ./eval_baseline.py --claude-models claude-sonnet-5,claude-haiku-4
    ./eval_baseline.py --tiktoken-encodings gpt-4
    ./eval_baseline.py --claude-models claude-sonnet-5 \
        --tiktoken-encodings o200k_base

Output (tag is derived from --claude-models/--tiktoken-encodings):
    results_static/trials<tag>.json
    results_static/summary<tag>.json
    results_static/responses<tag>.json
    results_static/report<tag>.txt
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import anthropic

# utils.py lives in eval/lib/
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))

from utils import (
    DATA_PATH, count_tokens_tiktoken, count_tokens_claude, word_count,
    boss_response_shape, save_json, write_lines, boss_session, boss_call,
)

RESULTS_DIR = os.path.join(_HERE, "results_static")

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

DEFAULT_TIKTOKEN_ENCODING = "gpt-4"

SHAPES = ("scalar", "tall_narrow", "short_wide", "tall_wide")

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
    """Construct a BOSS expression that evaulates to the first n rows of the
    dataset, selecting the key columns and the requested metrics."""
    cols = [["Symbol", k] for k in keys] + [["Symbol", m] for m in metrics]
    return ["Project",
               ["Slice",
                   ["Load", ["String", DATA_PATH]], ["Int", 0],["Int", n]
               ],
               *cols
           ]


QUERIES = []

for length, metrics in (("short", SHORT_METRICS), ("long", LONG_METRICS)):
    m1     = metrics[:1]
    m4     = metrics[:4]
    m_all  = metrics

    QUERIES.append({
        "id": f"SC-{length}", "shape": "scalar", "name_length": length,
        "description": f"Global mean of 1 metric, {length} column name",
        "boss_query": _groupby_rename(m1),
    })
    QUERIES.append({
        "id": f"TN-{length}", "shape": "tall_narrow", "name_length": length,
        "description": f"Per-country mean of 4 metrics, {length} column names",
        "boss_query": _groupby_rename(m4, keys=("code",)),
    })
    QUERIES.append({
        "id": f"SW-{length}", "shape": "short_wide", "name_length": length,
        "description": f"First 5 raw rows, 9 metrics, {length} column names",
        "boss_query": _slice_select(5, m_all, keys=("code",)),
    })
    QUERIES.append({
        "id": f"TW-{length}", "shape": "tall_wide", "name_length": length,
        "description": f"Per-country mean of 9 metrics, {length} column names",
        "boss_query": _groupby_rename(m_all, keys=("code",)),
    })


def measure_pair(q, col_session, row_session, claude_client,
                 tiktoken_encodings, claude_models) -> dict:
    """Runs the BOSS query pair ONCE and measures the response under every
    requested tokeniser"""
    col_proc, col_ids = col_session
    row_proc, row_ids = row_session

    array_query = q["boss_query"]

    base = {
        "id":          q["id"],
        "shape":       q["shape"],
        "name_length": q["name_length"],
        "description": q["description"],
    }

    def failed(error) -> dict:
        return {**base, "success": False, "error": error,
                "rows": 0, "cols": 0, "response_word_count": 0,
                "tokenizers": {}}

    col_resp = boss_call(col_proc, array_query, next(col_ids))
    if not col_resp["success"]:
        return failed(col_resp.get("error"))

    row_resp = boss_call(row_proc, array_query, next(row_ids))
    if not row_resp["success"]:
        return failed(row_resp.get("error"))

    col_text, row_text = col_resp["result"], row_resp["result"]

    tokenizers = {}
    rows = cols = 0
    for enc in tiktoken_encodings:
        shape_info = boss_response_shape(col_text, enc)
        rows, cols = shape_info["rows"], shape_info["cols"]
        c, r = count_tokens_tiktoken(col_text, enc), count_tokens_tiktoken(row_text, enc)
        tokenizers[enc] = {
            "kind": "tiktoken", "columnar": c, "rowrep": r,
            "ratio": round(r / c, 3) if c > 0 else 0.0,
            "avg_colname_tokens": shape_info["avg_colname_tokens"],
        }
    for model in claude_models:
        c = count_tokens_claude(claude_client, col_text, model)
        r = count_tokens_claude(claude_client, row_text, model)
        tokenizers[model] = {
            "kind": "claude", "columnar": c, "rowrep": r,
            "ratio": round(r / c, 3) if c > 0 else 0.0,
            "avg_colname_tokens": None,
        }

    return {
        **base,
        "success": True, "error": None,
        "rows": rows, "cols": cols,
        "response_word_count": word_count(row_text),
        "tokenizers": tokenizers,
        "columnar_response":    json.loads(col_text),
        "rowrepeated_response": json.loads(row_text),
    }


def summarise(trials: list, tokenizer_labels: list) -> dict:
    by_shape = {}
    for shape in SHAPES:
        short_t = next((t for t in trials if t["shape"] == shape and t["name_length"] == "short"), None)
        long_t  = next((t for t in trials if t["shape"] == shape and t["name_length"] == "long"), None)
        ok = short_t and long_t and short_t["success"] and long_t["success"]

        tok_summary = {}
        for label in tokenizer_labels:
            if not ok:
                tok_summary[label] = {"short_ratio": None, "long_ratio": None,
                                       "short_colname_tokens": None, "long_colname_tokens": None}
                continue
            s_tok, l_tok = short_t["tokenizers"][label], long_t["tokenizers"][label]
            tok_summary[label] = {
                "short_ratio": s_tok["ratio"], "long_ratio": l_tok["ratio"],
                "short_colname_tokens": s_tok["avg_colname_tokens"],
                "long_colname_tokens": l_tok["avg_colname_tokens"],
            }

        by_shape[shape] = {
            "rows": short_t["rows"] if ok else None,
            "cols": short_t["cols"] if ok else None,
            "tokenizers": tok_summary,
        }

    return {"by_shape": by_shape}


def write_report(trials, summary, path, tokenizer_labels):
    lines = [
        "Column-Name-Length Token Efficiency Baseline",
        "=" * 60,
        f"Generated  : {datetime.now(timezone.utc).isoformat()}",
        f"Tokenisers : {', '.join(tokenizer_labels)}",
        f"Data       : {DATA_PATH}",
        f"Queries    : {len(trials)}",
        "",
        "-" * 60,
    ]
    for shape in SHAPES:
        s = summary["by_shape"][shape]
        if s["rows"] is None:
            lines.append(f"  {shape:12s}  (failed)")
            continue
        lines.append(f"  {shape:12s}  rows={s['rows']:4d}  cols={s['cols']:2d}")
        for label in tokenizer_labels:
            t = s["tokenizers"][label]
            colname_note = (f"  colname avg: short={t['short_colname_tokens']:.1f}tok "
                             f"long={t['long_colname_tokens']:.1f}tok"
                             if t["short_colname_tokens"] is not None else "")
            lines.append(
                f"  {'':14s}[{label:22s}] short={t['short_ratio']:.2f}x  "
                f"long={t['long_ratio']:.2f}x{colname_note}"
            )

    lines += ["", "PER-QUERY DETAIL", "-" * 60]
    for t in trials:
        lines.append(f"  {t['id']:10s} [{t['shape']:11s}/{t['name_length']:5s}]  {t['description']}")
        if t["success"]:
            lines.append(f"  {'':10s}  rows={t['rows']:4d} cols={t['cols']:2d}")
            for label in tokenizer_labels:
                tok = t["tokenizers"][label]
                lines.append(
                    f"  {'':10s}  [{label:22s}] col={tok['columnar']:6d} "
                    f"row={tok['rowrep']:6d}  ratio={tok['ratio']:.2f}x"
                )
        else:
            lines.append(f"  {'':10s}  FAIL  error={t['error']}")

    write_lines(path, lines)


def main():
    p = argparse.ArgumentParser(
        description="Column-name-length token efficiency baseline"
    )
    p.add_argument(
        "--claude-models", default=[DEFAULT_CLAUDE_MODEL],
        type=lambda s: [m.strip() for m in s.split(",")],
        help="comma-separated Claude models to measure via count_tokens, "
        f"e.g. 'claude-sonnet-5,claude-haiku-4-5' (default: {DEFAULT_CLAUDE_MODEL})")
    p.add_argument(
        "--tiktoken-encodings", default=[DEFAULT_TIKTOKEN_ENCODING],
        type=lambda s: [e.strip() for e in s.split(",")],
        help="comma-separated tiktoken model/encoding names, "
        f"e.g. 'gpt-4,o200k_base' (default: {DEFAULT_TIKTOKEN_ENCODING})")
    args = p.parse_args()
    claude_models      = args.claude_models
    tiktoken_encodings = args.tiktoken_encodings
    tokenizer_labels   = tiktoken_encodings + claude_models

    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Column-Name-Length Token Efficiency Baseline")
    print(f"Data: {DATA_PATH}")
    print(f"Tokenisers: {', '.join(tokenizer_labels)}")
    print("=" * 60)

    claude_client = anthropic.Anthropic()

    trials    = []
    responses = []

    with (
        boss_session(result_format="columnarjson") as col_session,
        boss_session(result_format="rowrepjson") as row_session,
    ):
        for q in QUERIES:
            print(f"  {q['id']:10s} [{q['shape']:11s}/{q['name_length']:5s}] "
                  f"{q['description'][:45]}...", end=" ", flush=True)
            result = measure_pair(q, col_session, row_session, claude_client,
                                   tiktoken_encodings, claude_models)

            if result["success"]:
                responses.append({
                    "id":                   result["id"],
                    "description":          result["description"],
                    "columnar_response":    result["columnar_response"],
                    "rowrepeated_response": result["rowrepeated_response"],
                })
                ratios = "  ".join(
                    f"{label}={result['tokenizers'][label]['ratio']:.2f}x"
                    for label in tokenizer_labels
                )
                print(f"rows={result['rows']} cols={result['cols']} {ratios}")
            else:
                print(f"FAILED: {result['error']}")

            trials.append(
                {k: v for k, v in result.items()
                if k not in ("columnar_response", "rowrepeated_response")})

    summary = summarise(trials, tokenizer_labels)

    tag_parts = [
        "+".join(m.removeprefix("claude-") for m in claude_models),
        "+".join(tiktoken_encodings),
    ]
    tag = "_" + "_".join(tag_parts)

    save_json(f"{RESULTS_DIR}/trials{tag}.json", trials)
    save_json(f"{RESULTS_DIR}/responses{tag}.json", responses)
    save_json(f"{RESULTS_DIR}/summary{tag}.json", summary)
    write_report(trials, summary,
        f"{RESULTS_DIR}/report{tag}.txt", tokenizer_labels
    )
    print(f"\nResults saved to {RESULTS_DIR}/ (tag: {tag})")


if __name__ == "__main__":
    main()
