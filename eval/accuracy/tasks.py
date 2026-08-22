"""
Task instantiation for the accuracy evaluation. Types:
- LOOKUP: return the value of a metric (column) on a given date. The target row
  must have a locally-unique value within a small window of neighbors.
- EXTREMUM: find the maximum value of a given column. Must be a single row,
  must beat the runner-up by more than the scoring tolerance
- AGGREGATE: a plain mean over one column. The column must have at least one
  non-null value.
"""

from score import REL_TOL

NEIGHBOR_WINDOW = 3      # LOOKUP: rows on each side that must hold a different value
EXTREMUM_MARGIN = 0.005  # EXTREMUM: max must exceed runner-up by more than this
MAX_DRAWS = 500

TASKS = ("lookup", "extremum", "aggregate")


def _col_values(table, col) -> list:
    i = table.columns.index(col)
    return [row[i] for row in table.rows]


def _dates(table) -> list[str]:
    return _col_values(table, "date")


def _differs(a, b) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) > REL_TOL * max(abs(a), abs(b), 1e-9)


def _metric_cols(table) -> list[str]:
    return [c for c in table.columns if c != "date"]


def instantiate(task, table, rng):
    """Sample a target from the neutral Table, compute the expected answer,
    and form the question text."""
    if task == "lookup":
        return _lookup(table, rng)
    if task == "extremum":
        return _extremum(table, rng)
    if task == "aggregate":
        return _aggregate(table, rng)
    raise ValueError(f"unknown task: {task}")


def _lookup(table, rng):
    dates, cols = _dates(table), _metric_cols(table)
    for _ in range(MAX_DRAWS):
        col = rng.choice(cols)              # target column
        vals = _col_values(table, col)      # target column's values
        row = rng.randrange(table.nrows)    # target row
        v = vals[row]                       # the value at that row/column (the answer)
        if v is None:
            continue
        lo, hi = max(0, row - NEIGHBOR_WINDOW), min(table.nrows, row + NEIGHBOR_WINDOW + 1)
        if all(_differs(v, vals[j]) for j in range(lo, hi) if j != row):
            return {
                "task": "lookup",
                "question": (f'What was {col} on {dates[row]}? '
                             f'Answer with ONLY a JSON object: {{"answer": <number>}}. No prose.'),
                "expected": v,
                "target": {"column": col, "date": dates[row],
                           "row": row, "position": row / max(table.nrows - 1, 1)},
            }
    raise RuntimeError("no LOOKUP target with locally-unique value found")


def _extremum(table, rng):
    dates, cols = _dates(table), _metric_cols(table)
    for col in rng.sample(cols, len(cols)):
        vals = _col_values(table, col)
        # extract the non-null values and their row indices
        present = [(v, j) for j, v in enumerate(vals) if v is not None]
        if len(present) < 2:
            continue
        vmax, row = max(present)
        # exclude monotonically increasing/decreasing columns,
        # which always peak at the first or last row
        if row == 0 or row == table.nrows - 1:
            continue
        # the max must be a single row
        if sum(1 for v, _ in present if not _differs(v, vmax)) != 1:
            continue
        second = max(v for v, _ in present if _differs(v, vmax))
        if (vmax - second) <= EXTREMUM_MARGIN * abs(vmax):
            continue
        return {
            "task": "extremum",
            "question": (f'On which date was {col} highest, and what was its value? '
                         f'Answer with ONLY a JSON object: '
                         f'{{"answer": {{"date": "YYYY-MM-DD", "value": <number>}}}}. No prose.'),
            "expected": {"date": dates[row], "value": vmax},
            "target": {"column": col, "date": dates[row],
                       "row": row, "position": row / max(table.nrows - 1, 1)},
        }
    raise RuntimeError("no EXTREMUM column with a clear-margin max found")


def _aggregate(table, rng):
    cols = _metric_cols(table)
    for col in rng.sample(cols, len(cols)):
        vals = [v for v in _col_values(table, col) if v is not None]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        return {
            "task": "aggregate",
            "question": (f'What is the mean of {col} over this period? '
                         f'Answer with ONLY a JSON object: {{"answer": <number>}}. No prose.'),
            "expected": avg,
            "target": {"column": col, "n": len(vals)},
        }
    raise RuntimeError("no AGGREGATE column with any non-null values found")
