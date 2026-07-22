"""
Task instantiation for the accuracy pilot: LOOKUP, EXTREMUM, and AGGREGATE.

instantiate(task, table, rng) samples a target from the neutral Table, computes
the expected answer in Python (the ground truth), and returns the question text
— identical across both serializations, so the representation is the only
difference between conditions.

Target-sampling controls (DESIGN.md §6, tightened for this dataset):

- LOOKUP: the OWID smoothed columns hold values constant across runs of
  adjacent days, and a positional misread that lands on a neighbor with the
  same value would be scored correct — invisible to the study. So a LOOKUP
  target must differ from every value within NEIGHBOR_WINDOW rows of it (by
  more than the scoring tolerance), making an off-by-a-few-rows slip always
  detectable.
- EXTREMUM: the max must be a single row (an exact-tie plateau at the top makes
  the expected date ambiguous), must beat the runner-up by more than the
  scoring tolerance (so the value tolerance can never blur max vs runner-up —
  a stricter margin is unrealistic for daily epidemic curves, where the
  runner-up is the adjacent day), and must sit on an *interior* row: cumulative
  columns peak on the last row every time, which a model can answer by recency
  without reading the layout at all.
- AGGREGATE: a plain mean over one column, no target row at all. Unlike LOOKUP
  and EXTREMUM, the answer doesn't need to be attributed to any particular
  row/date — so it has no analogous validity constraints (no plateau, margin,
  or interior-row check needed) and, mechanistically, no exposure to the
  positional row-binding failure mode LOOKUP/EXTREMUM showed on columnar: there
  is no row index to get wrong. The only requirement is that the column has at
  least one non-null value.

LOOKUP and EXTREMUM record the target's row position so layout effects can
later be separated from recency effects; AGGREGATE has no target row to record.
"""

REL_TOL = 0.005          # scoring tolerance (score.py uses the same value)
NEIGHBOR_WINDOW = 3      # LOOKUP: rows on each side that must hold a different value
EXTREMUM_MARGIN = 0.005  # EXTREMUM: max must exceed runner-up by more than this
MAX_DRAWS = 500

TASKS = ("lookup", "extremum", "aggregate")


def _col_values(table, col):
    i = table.columns.index(col)
    return [row[i] for row in table.rows]


def _dates(table):
    return _col_values(table, "date")


def _differs(a, b):
    if a is None or b is None:
        return True
    return abs(a - b) > REL_TOL * max(abs(a), abs(b), 1e-9)


def _metric_cols(table):
    return [c for c in table.columns if c != "date"]


def instantiate(task, table, rng):
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
        col = rng.choice(cols)
        vals = _col_values(table, col)
        row = rng.randrange(table.nrows)
        v = vals[row]
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
    cols = rng.sample(cols, len(cols))
    for col in cols:
        vals = _col_values(table, col)
        present = [(v, j) for j, v in enumerate(vals) if v is not None]
        if len(present) < 2:
            continue
        vmax, row = max(present)
        # exclude first and last rows, which are always the max for cumulative columns
        if row == 0 or row == table.nrows - 1:
            continue
        # the max must be a single row since multiple equal values at the top
        # would make the expected date ambiguous
        if sum(1 for v, j in present if not _differs(v, vmax)) != 1:
            continue
        second = max(v for v, j in present if _differs(v, vmax))
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
    cols = rng.sample(_metric_cols(table), len(_metric_cols(table)))
    for col in cols:
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
