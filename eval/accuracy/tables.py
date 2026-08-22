"""
Fixture tables for the accuracy study — produced by the real BOSS server.
`build()` runs one Load->Filter->Project query through boss_client once per
result format under test.
"""

import json
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import boss_client as bc

DATA_PATH = str(Path(__file__).resolve().parent.parent.parent
                / "data" / "owid-covid-data-full.csv")

METRIC_COLUMNS = [
    "new_cases_smoothed_per_million",
    "new_deaths_smoothed_per_million",
    "hosp_patients_per_million",
    "icu_patients_per_million",
    "people_fully_vaccinated_per_hundred",
]

FORMATS = ("typedcolumnarjson", "columnarjson", "indexedcolumnarjson",
           "positionalrowsjson", "arrayofobjectsjson")

AUTO_FORMAT = "auto"

GROUND_TRUTH_FORMAT = "typedcolumnarjson"

# Fixture windows are sampled from this pool. Only countries with *daily*
# reporting granularity qualify: FRA/ESP/DEU hold the smoothed cases/deaths
# columns constant for a week at a time and DEU never reports hosp_patients.
# GBR/ITA/USA/BEL have ~60 distinct daily values in every column over the
# candidate period.
COUNTRY_POOL = ["GBR", "ITA", "USA", "BEL"]
WINDOW_DAYS = 60
WINDOW_EARLIEST = date(2020, 11, 1)   # ICU/hosp series are null before this
WINDOW_LATEST_START = date(2022, 1, 31)

# reject a window sharing more than this fraction of its days with an
# already-used same-country window
OVERLAP_THRESHOLD = 0.5


def sample_window(rng, days=WINDOW_DAYS):
    """A random (code, start, end) fixture window from the pool."""
    span = (WINDOW_LATEST_START - WINDOW_EARLIEST).days
    start = WINDOW_EARLIEST + timedelta(days=rng.randrange(span + 1))
    end = start + timedelta(days=days - 1)
    return rng.choice(COUNTRY_POOL), start.isoformat(), end.isoformat()


def _overlap_days(start1: str, end1: str, start2: str, end2: str) -> int:
    """Count how many overlapping days two date ranges share"""
    s1, e1 = date.fromisoformat(start1), date.fromisoformat(end1)
    s2, e2 = date.fromisoformat(start2), date.fromisoformat(end2)
    latest_start, earliest_end = max(s1, s2), min(e1, e2)
    return max(0, (earliest_end - latest_start).days + 1)


def overlaps_used(key, used, threshold=OVERLAP_THRESHOLD) -> bool:
    """True if key's window shares too many days (exceeds threshold) with a
    same-country window already in `used`"""
    code, start, end = key
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    for u_code, u_start, u_end in used:
        if u_code == code and _overlap_days(start, end, u_start, u_end) / days > threshold:
            return True
    return False


def fixture_query(code, start, end):
    """One-shot Load->Filter->Project pulling date + the five metric columns."""
    pred = ["And",
            ["Equal", ["Symbol", "code"], ["String", code]],
            ["And",
             ["GreaterEqual", ["Symbol", "date"], ["String", start]],
             ["LessEqual", ["Symbol", "date"], ["String", end]]]]
    return (["Project", ["Filter", ["Load", ["String", DATA_PATH]], pred],
             ["Symbol", "date"]]
            + [["Symbol", c] for c in METRIC_COLUMNS])


@dataclass
class Fixture:
    table: bc.Table   # in typedcolumnarjson format to compute ground truth
    served: dict      # format name -> json text returned by boss_client
    resolved: dict    # format name -> format name actually served
                      # (auto may resolve to any of the others)


@contextmanager
def sessions(formats=FORMATS):
    """One persistent BOSS session per format, reused across every sampled
    window by build()"""
    needed = list(dict.fromkeys([GROUND_TRUTH_FORMAT, *formats]))
    with ExitStack() as stack:
        yield {f: stack.enter_context(bc.session(result_format=f)) for f in needed}


def build(procs, code, start, end, task) -> Fixture:
    """Call boss_evaluate once per format in 'procs' and construct a Fixture"""
    q = fixture_query(code, start, end)
    served = {}
    for fmt, proc in procs.items():
        intent = task if fmt == AUTO_FORMAT else None
        served[fmt] = bc.evaluate(*proc, q, response_intent=intent)
    table = bc.parse_columnar(served[GROUND_TRUTH_FORMAT])
    if table.nrows == 0:
        raise bc.BossError(f"empty fixture table for {code} {start}..{end}")
    resolved = {fmt: detect_served_format(text) if fmt == AUTO_FORMAT else fmt
                for fmt, text in served.items()}
    return Fixture(table, served, resolved)


def detect_served_format(served: str) -> str:
    """Best-effort guess at which format a served auto payload resolved to,
      - typedcolumnarjson keeps the ["Table", ...] head
      - positionalrowsjson starts each result with a ["Schema", ...] row
      - arrayofobjectsjson is a JSON array of objects (deserialised as a list of dicts)
      - indexedcolumnarjson's cells are [row_index, value] pairs
      - columnarjson is whatever's left: plain columns of bare values
    """
    data = json.loads(served)
    if not (isinstance(data, list) and data):
        return "columnarjson"
    head = data[0]
    if head == "Table":
        return "typedcolumnarjson"
    if isinstance(head, dict):
        return "arrayofobjectsjson"
    if isinstance(head, list) and head:
        if head[0] == "Schema":
            return "positionalrowsjson"
        if len(head) > 1 and isinstance(head[1], list) and len(head[1]) == 2 \
                and isinstance(head[1][0], int):
            return "indexedcolumnarjson"
    return "columnarjson"
