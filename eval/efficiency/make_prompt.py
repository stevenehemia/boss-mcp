"""
Generate this study's system prompt

Usage:
    python3 make_prompt.py > prompt.arrayjson.txt
"""

import json
from pathlib import Path

DATA_PATH = str(Path(__file__).resolve().parent.parent.parent
                / "data" / "owid-covid-data-full.csv")


def render(expr):
    return json.dumps(expr, separators=(",", ":"))


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


def build_prompt():
    lines = [
        "You are a data analyst with access to a COVID-19 dataset via the BOSS query engine.",
        "",
        f"Dataset path: {DATA_PATH}",
        "",
        'BOSS expressions are written as positional arrays — operators are ["Op", arg, ...] '
        'and atoms are typed pairs like ["String", "s"] or ["Symbol", "col"].',
        "",
        "Operators:",
    ]
    for desc, ex in OPERATORS:
        lines.append(f"  {desc}:")
        lines.append(f"    {render(ex)}")

    lines += ["", "Aggregations (used inside GroupBy):"]
    lines += [f"    {render(ex)}" for ex in AGGREGATIONS]

    lines += ["", "Predicates:"]
    lines += [f"    {render(ex)}" for ex in PREDICATES]

    lines += ["", "Atoms (String, Symbol/column-ref, Integer, Real, Boolean, Int/32-bit):"]
    lines += [f"    {render(ex)}" for ex in ATOMS]

    lines += [
        "",
        "Worked example — mean new_cases_smoothed_per_million per country during 2022, worst first:",
        f"    {render(WORKED)}",
        "",
        "Rules:",
        "  - CountAll takes NO arguments inside GroupBy.",
        '  - An aggregation names its output column "agg(col)" — e.g. Mean of '
        '"new_cases_smoothed_per_million" becomes "mean(new_cases_smoothed_per_million)". '
        "Reference that name in a later OrderBy/Filter.",
        '  - Slice offset and count must be ["Int", n] (32-bit), not ["Integer", n].',
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


if __name__ == "__main__":
    print(build_prompt())
