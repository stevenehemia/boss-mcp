"""
Analyse a pilot results file: accuracy per (task x format) with Wilson 95% CIs,
the columnar-minus-rowrep gap per task, and whether the gap's sign flips
between column-wise (extremum) and record-wise (lookup) tasks — the study's
central hypothesis.

Usage: python3 analyse_accuracy.py [results/pilot_*.jsonl]
(defaults to the newest pilot_*.jsonl under results/)
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def wilson(k, n, z=1.96):
    """Wilson 95% score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, center - half), min(1.0, center + half))


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.exists():
            path = HERE / "results" / sys.argv[1]
    else:
        candidates = sorted((HERE / "results").glob("pilot_*.jsonl"))
        if not candidates:
            sys.exit("no results/pilot_*.jsonl found")
        path = candidates[-1]

    recs = [json.loads(line) for line in open(path)]
    print(f"{path.name}: {len(recs)} calls, model={recs[0]['model']}, "
          f"nrows={recs[0]['nrows']}\n")

    cells = defaultdict(lambda: [0, 0, 0])  # (task, fmt) -> [correct, n, malformed]
    for r in recs:
        c = cells[(r["task"], r["format"])]
        c[1] += 1
        c[0] += r["correct"]
        c[2] += r.get("malformed_first_try", False)

    tasks = sorted({t for t, _ in cells})
    fmts = ("columnarjson", "rowrepjson")

    print(f"{'task':<10} {'format':<13} {'acc':>6}  {'95% CI':<16} {'malformed':>9}")
    for t in tasks:
        for f in fmts:
            k, n, m = cells[(t, f)]
            p, lo, hi = wilson(k, n)
            print(f"{t:<10} {f:<13} {p:>5.0%}  [{lo:>4.0%}, {hi:>4.0%}]  "
                  f"{k}/{n:<4} {m:>6}")
        print()

    print("gap = accuracy(columnar) - accuracy(rowrep):")
    signs = {}
    for t in tasks:
        kc, nc, _ = cells[(t, "columnarjson")]
        kr, nr, _ = cells[(t, "rowrepjson")]
        gap = kc / nc - kr / nr if nc and nr else 0.0
        signs[t] = gap
        print(f"  {t:<10} {gap:+.0%}")

    if "lookup" in signs and "extremum" in signs:
        flip = signs["extremum"] > 0 > signs["lookup"]
        print(f"\nsign-flip (extremum favors columnar AND lookup favors rowrep): "
              f"{'YES' if flip else 'no'}")


if __name__ == "__main__":
    main()
