"""
Stimulus tables for the accuracy study — produced by the real BOSS server.

`build()` runs one Load->Filter->Project query through boss_client three times —
once per native result format for the byte-exact strings that go into the
prompt, once parsed into the neutral Table that ground truth is computed from.
"""

import sys
from contextlib import contextmanager
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

# Trial stimuli are sampled from this pool. Only countries with *daily*
# reporting granularity qualify: FRA/ESP/DEU hold the smoothed cases/deaths
# columns constant for a week at a time and DEU never reports hosp_patients.
# GBR/ITA/USA/BEL have ~60 distinct daily values in every column over the
# candidate period.
COUNTRY_POOL = ["GBR", "ITA", "USA", "BEL"]
WINDOW_DAYS = 60
WINDOW_EARLIEST = date(2020, 11, 1)       # ICU/hosp series populated from here
WINDOW_LATEST_START = date(2022, 1, 31)


def sample_window(rng, days=WINDOW_DAYS):
    """A random (code, start, end) stimulus window from the pool."""
    span = (WINDOW_LATEST_START - WINDOW_EARLIEST).days
    start = WINDOW_EARLIEST + timedelta(days=rng.randrange(span + 1))
    end = start + timedelta(days=days - 1)
    return rng.choice(COUNTRY_POOL), start.isoformat(), end.isoformat()


# reject a window sharing more than this fraction of its days with an
# already-used same-country window
OVERLAP_THRESHOLD = 0.5


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
    for u_code, u_start, u_end in used:
        if u_code == code and _overlap_days(start, end, u_start, u_end) / WINDOW_DAYS > threshold:
            return True
    return False


def stimulus_query(code, start, end):
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
class Stimulus:
    table: bc.Table   # neutral, row-major — ground truth is computed from this
    columnar: str     # server's columnarjson output, byte-exact
    rowrep: str       # server's rowrepjson output, byte-exact

    def served(self, fmt):
        return {"columnarjson": self.columnar, "rowrepjson": self.rowrep}[fmt]


@contextmanager
def sessions():
    """Two persistent BOSS sessions (columnar + rowrep), reused across every
    sampled window by build() below instead of relaunching the server (a fresh
    subprocess + JSON-RPC handshake) on every fetch. Each query still re-runs
    its own Load over the CSV -- BOSS doesn't cache across calls -- so this
    saves process-startup overhead, not the per-query scan itself."""
    with bc.session(result_format="columnarjson") as col, \
         bc.session(result_format="rowrepjson") as row:
        yield col, row


def build(col, row, code, start, end) -> Stimulus:
    """col/row: (proc, ids) pairs from an open sessions() context."""
    q = stimulus_query(code, start, end)
    columnar = bc.evaluate(*col, q)
    rowrep = bc.evaluate(*row, q)
    table = bc.parse_columnar(columnar)
    if table.nrows == 0:
        raise bc.BossError(f"empty stimulus table for {code} {start}..{end}")
    return Stimulus(table, columnar, rowrep)
