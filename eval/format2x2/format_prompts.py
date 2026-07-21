"""
Generate the system prompt for one query format from a single source of
operators / examples / rules. The experiment explores two query formats
(arrayjson and objectjson).

    arrayjson  : positional arrays   ["Op", arg, ...]   atoms ["String","s"]
    objectjson : tagged objects       {"type":"call","head":"Op","args":[...]}
                                       atoms {"type":"string","value":"s"}

Usage:
    python3 format_prompts.py arrayjson > prompt.arrayjson.txt
    python3 format_prompts.py objectjson > prompt.objectjson.txt
"""

import json
import sys

from questions import DATA_PATH

# ── Encoding ────────────────────────────────────────────────────────────────────
# 32-bit Int needs its own object encoding ({"type":"int"}); BOSS's Slice rejects
# a 64-bit long in the offset/count slots. This converter special-cases it.
_ATOM_TYPE = {
    "Integer": "long", "Real": "double", "String": "string",
    "Symbol": "symbol", "Boolean": "bool", "Int": "int",
}


def to_regular(expr):
    """ExpressionJSON (array form) -> object form, Int-aware."""
    if isinstance(expr, list) and len(expr) == 2 and isinstance(expr[0], str) and expr[0] in _ATOM_TYPE:
        return {"type": _ATOM_TYPE[expr[0]], "value": expr[1]}
    if isinstance(expr, list) and expr:
        return {"type": "call", "head": expr[0], "args": [to_regular(a) for a in expr[1:]]}
    return expr


def render(expr, query_format):
    obj = expr if query_format == "arrayjson" else to_regular(expr)
    return json.dumps(obj, separators=(",", ":"))


# ── Shared reference (one source of truth, expressed in ExpressionJSON) ──────────

OPERATORS = [
    ("Load — read a CSV (always an absolute path)",
     ["Load", ["String", "<absolute_path>"]]),
    ("Filter — keep rows matching a predicate",
     ["Filter", ["Symbol", "table"],
      ["Greater", ["Symbol", "new_cases_per_million"], ["Real", 1000]]]),
    ("GroupBy — aggregate, grouped by one or more key columns",
     ["GroupBy", ["Symbol", "table"],
      ["Mean", ["Symbol", "new_cases_smoothed_per_million"]], ["Symbol", "code"]]),
    ("OrderBy — sort rows by a list of columns",
     ["OrderBy", ["Symbol", "table"], ["List", ["Desc", ["Symbol", "mean_value"]]]]),
    ("Project — keep only the listed columns",
     ["Project", ["Symbol", "table"], ["Symbol", "code"], ["Symbol", "date"]]),
    ("Join — match two tables on equal keys",
     ["Join", ["Symbol", "left"], ["Symbol", "right"],
      ["Equal", ["Symbol", "code"], ["Symbol", "code"]]]),
    ("Slice — offset then count",
     ["Slice", ["Symbol", "table"], ["Int", 0], ["Int", 5]]),
]

AGGREGATIONS = [
    ["Sum", ["Symbol", "col"]],
    ["Mean", ["Symbol", "col"]],
    ["Max", ["Symbol", "col"]],
    ["Min", ["Symbol", "col"]],
    ["CountAll"],
]

PREDICATES = [
    ["Equal", ["Symbol", "col"], ["String", "value"]],
    ["Greater", ["Symbol", "col"], ["Real", 0]],
    ["Less", ["Symbol", "col"], ["Real", 0]],
    ["And", ["Symbol", "pred1"], ["Symbol", "pred2"]],
    ["Or", ["Symbol", "pred1"], ["Symbol", "pred2"]],
    ["Not", ["Symbol", "pred"]],
]

ATOMS = [
    ["String", "text"],
    ["Symbol", "column_name"],
    ["Integer", 42],
    ["Real", 3.14],
    ["Boolean", True],
    ["Int", 5],
]

# An example of a fully worked query
WORKED = ["OrderBy",
             ["GroupBy",
                 ["Filter",
                     ["Load", ["String", DATA_PATH]],
                     ["And",
                         ["Greater", ["Symbol", "date"], ["String", "2021-12-31"]],
                         ["Less", ["Symbol", "date"], ["String", "2023-01-01"]]
                     ]
                 ],
                 ["Mean", ["Symbol", "new_cases_smoothed_per_million"]],
                 ["Symbol", "code"]
             ],
             ["List", ["Desc", ["Symbol", "mean(new_cases_smoothed_per_million)"]]]
         ]


def build_prompt(query_format):
    if query_format == "arrayjson":
        encoding = ('positional arrays — operators are ["Op", arg, ...] and atoms are '
                    'typed pairs like ["String", "s"] or ["Symbol", "col"]')
        slice_rule = '  - Slice offset and count must be ["Int", n] (32-bit), not ["Integer", n].'
    else:
        encoding = ('nested objects — operators are {"type":"call","head":"Op","args":[...]} '
                    'and atoms are typed objects like {"type":"string","value":"s"} or '
                    '{"type":"symbol","value":"col"}')
        slice_rule = ('  - Slice offset and count must be {"type":"int","value":n} (32-bit), '
                      'not {"type":"long","value":n}.')

    lines = [
        "You are a data analyst with access to a COVID-19 dataset via the BOSS query engine.",
        "",
        f"Dataset path: {DATA_PATH}",
        "",
        f"BOSS expressions are written as {encoding}.",
        "",
        "Operators:",
    ]
    for desc, ex in OPERATORS:
        lines.append(f"  {desc}:")
        lines.append(f"    {render(ex, query_format)}")

    lines += ["", "Aggregations (used inside GroupBy):"]
    lines += [f"    {render(ex, query_format)}" for ex in AGGREGATIONS]

    lines += ["", "Predicates:"]
    lines += [f"    {render(ex, query_format)}" for ex in PREDICATES]

    lines += ["", "Atoms (String, Symbol/column-ref, Integer, Real, Boolean, Int/32-bit):"]
    lines += [f"    {render(ex, query_format)}" for ex in ATOMS]

    lines += [
        "",
        "Worked example — mean new_cases_smoothed_per_million per country during 2022, worst first:",
        f"    {render(WORKED, query_format)}",
        "",
        "Rules:",
        "  - CountAll takes NO arguments inside GroupBy.",
        '  - An aggregation names its output column "agg(col)" — e.g. Mean of '
        '"new_cases_smoothed_per_million" becomes "mean(new_cases_smoothed_per_million)". '
        "Reference that name in a later OrderBy/Filter.",
        slice_rule,
        "  - Always use the full absolute dataset path in Load.",
        "  - `boss_evaluate` is your ONLY available tool. Call it immediately with your BOSS "
        "expression — do not attempt Bash, Python, file reads, or any other approach. The MCP "
        "server is already connected and ready.",
        "  - Retrieve everything you need in a SINGLE boss_evaluate call shaped as "
        "Load -> Filter -> Project (selecting the columns the question asks for). Do NOT "
        "aggregate (GroupBy) or paginate (Slice) — pull the daily rows themselves.",
        "  - Base your answer on the rows of the Table returned by boss_evaluate, reading the "
        "daily values to reason about it, and explain your findings in natural language.",
    ]
    return "\n".join(lines)


def main():
    query_format = sys.argv[1] if len(sys.argv) > 1 else "arrayjson"
    if query_format not in ("arrayjson", "objectjson"):
        sys.exit(f"unknown query format {query_format!r}; use arrayjson or objectjson")
    print(build_prompt(query_format))


if __name__ == "__main__":
    main()
