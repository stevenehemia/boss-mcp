r"""
Questions for the efficiency evaluation

Question tiering
    tier rows  columnar  positional  indexed  objects
    b1    171    27.4%      28.1%     43.9%   103.2%  <- objects over
    b2    395    62.6%      64.1%    103.3%   238.3%  <- indexed over
    b3    520    81.9%      83.9%    136.0%   313.3%  <- mid anchor
    b4    620    97.5%     100.1%    162.5%   373.6%  <- positional over
    b5    660   103.9%     106.5%    173.2%   397.8%  <- none fits
Values are percent of the 50,000-character cap
"""

from datetime import date, timedelta
from pathlib import Path

DATA_PATH = str(Path(__file__).resolve().parent.parent.parent
                / "data" / "owid-covid-data-full.csv")

REPLICATES = 5

METRICS = [
    "new_cases_smoothed_per_million",
    "new_deaths_smoothed_per_million",
    "hosp_patients_per_million",
    "icu_patients_per_million",
    "reproduction_rate",
    "total_cases_per_million",
    "total_deaths_per_million",
    "stringency_index",
]

_METRIC_CLAUSE = (
    "smoothed new cases and new deaths per million, hospital and ICU patients "
    "per million, the reproduction rate, cumulative cases and deaths per "
    "million, and the stringency index"
)

# Make the agent's response gradeable (analyse_results.py's scoring phase)
_ANSWER_TAIL = (
    " Finish your reply with a single JSON object on its own final line and "
    "nothing after it: {\"days_covered\": <number of daily rows your answer is "
    "based on>, \"peak_cases_date\": \"YYYY-MM-DD\", \"peak_cases_value\": "
    "<number>, \"peak_hosp_date\": \"YYYY-MM-DD\"} — where the two peak dates "
    "are the single highest day in the period for smoothed new cases per "
    "million and for hospital patients per million respectively, and "
    "peak_cases_value is that highest smoothed new-cases-per-million figure."
)

CONDITIONS = {
    "columnar":     "columnarjson",
    "indexed":      "indexedcolumnarjson",
    "positional":   "positionalrowsjson",
    "objects":      "arrayofobjectsjson",
    "auto":         "auto",
}
ALL_CONDITIONS = tuple(CONDITIONS)

CONDITION_BUDGETS = {"auto": 50_000}

SHAPES = ("b1", "b2", "b3", "b4", "b5")

# shape -> (code, exclusive-start, exclusive-end)
WINDOWS = {
    "b1": ("ITA", "2020-08-24", "2021-02-12"),
    "b2": ("ITA", "2020-05-03", "2021-06-03"),
    "b3": ("ITA", "2020-06-22", "2021-11-25"),
    "b4": ("ITA", "2020-03-17", "2021-11-28"),
    "b5": ("ITA", "2020-02-23", "2021-12-15"),
}

ROWS = {"b1": 171, "b2": 395, "b3": 520, "b4": 620, "b5": 660}

CAP_CHARS = 50_000
CAP_TOKENS = 25_000

# Measured live against build/boss_mcp on the query each question specifies.
RESULT_CHARS = {
    "b1": {"columnar":  13_716, "positional":  14_051, "indexed": 21_960, "objects":  51_610},
    "b2": {"columnar":  31_289, "positional":  32_072, "indexed": 51_629, "objects": 119_135},
    "b3": {"columnar":  40_930, "positional":  41_963, "indexed": 68_020, "objects": 156_651},
    "b4": {"columnar":  48_768, "positional":  50_001, "indexed": 81_258, "objects": 186_789},
    "b5": {"columnar":  51_946, "positional":  53_259, "indexed": 86_596, "objects": 198_887},
}

def over_cap(shape, condition):
    """Characters, not tokens -- the unit the host enforces. This ladder is
    entirely about which side of that line a format lands on, so the distinction
    matters more here than anywhere."""
    return RESULT_CHARS[shape].get(condition, 0) > CAP_CHARS

assert not over_cap("b3", "columnar") and not over_cap("b3", "positional")
assert not over_cap("b4", "columnar") and over_cap("b4", "positional")
assert over_cap("b5", "columnar") and over_cap("b5", "positional")
assert over_cap("b1", "objects") and not over_cap("b1", "indexed")
assert over_cap("b2", "indexed") and not over_cap("b2", "positional")

_CPT = {"columnar": 1.72, "positional": 1.73, "indexed": 2.02, "objects": 2.97}
RESULT_TOKENS = {s: {c: int(n / _CPT[c]) for c, n in row.items()}
                 for s, row in RESULT_CHARS.items()}

_BLURB = ("Identify every distinct wave in that stretch, when each one crested, "
          "and how hospital and ICU pressure tracked behind cases each time.")


def _pretty(iso, offset_days):
    """`WINDOWS` bounds are EXCLUSIVE; a question names the INCLUSIVE endpoints.
    Deriving the prose from the same constant the ground truth is generated from
    means the two cannot drift apart in a later edit."""
    d = date.fromisoformat(iso) + timedelta(days=offset_days)
    return f"{d.day} {d.strftime('%B %Y')}"


def _q(qid, shape):
    _code, start, end = WINDOWS[shape]
    return {"id": qid, "shape": shape, "conditions": ALL_CONDITIONS,
            "text": (f"Walk me through Italy's daily record from "
                     f"{_pretty(start, 1)} through {_pretty(end, -1)}, day by "
                     f"day — " + _METRIC_CLAUSE + ". " + _BLURB + _ANSWER_TAIL)}


QUESTIONS = [
    _q("B1", "b1"),
    _q("B2", "b2"),
    _q("B3", "b3"),
    _q("B4", "b4"),
    _q("B5", "b5"),
]
