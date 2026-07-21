"""
QUESTIONS are natural analyst questions sized so the NSM/DSM (rowrep vs columnar)
result-format effect can be measured cleanly in one shot. Design considerations:

  1. Long column names. The rowrep penalty is amplified by long column names
     and row count. The questions steer to the long "..._per_million" /
     "..._per_hundred" / "..._smoothed_..." metrics.
  3. The answer must require reading the daily ROWS (wave shape, peak timing,
     case-vs-death relationship over time), which BOSS cannot compute
     server-side — so the agent retrieves the series and reasons over it rather
     than reducing it to a scalar.

All four types hold the same 5-column schema and vary only the row count.
  xs ≈  90 rows
  s  ≈ 184 rows
  m  ≈ 275 rows
  l  ≈ 365 rows
"""

from pathlib import Path

# boss-mcp/eval/format2x2/questions.py -> boss-mcp/data/owid-covid-data-full.csv
DATA_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / "owid-covid-data-full.csv")

SHAPES = ("xs", "s", "m", "l")

QUESTIONS = [
    # ── xs: Germany, Omicron winter ~90 daily rows x 5 long columns ───────────
    {"id": "XS", "shape": "xs", "text":
        "Walk me through how Germany's Omicron wave played out over the winter of "
        "2021 into 2022. Going day by day — smoothed new cases per million, "
        "smoothed new deaths per million, hospital patients per million, ICU "
        "patients per million, and the share of people fully vaccinated — when "
        "did the wave crest, and how quickly did hospital and ICU pressure follow "
        "the case peak?"},

    # ── s: France, second half of 2021 ~184 daily rows x 5 long columns ───────
    {"id": "S", "shape": "s", "text":
        "Take me through the second half of 2021 in France, day by day — the "
        "smoothed new cases and new deaths per million, hospital and ICU patients "
        "per million, and how the share of people fully vaccinated was climbing. "
        "How did the autumn wave build, and as vaccination coverage increases, "
        "did it seem to turn any less deadly?"},

    # ── m: Spain, spring to end of 2021 ~275 daily rows x 5 long columns ──────
    {"id": "M", "shape": "m", "text":
        "Read through Spain's daily figures from spring to the end of 2021 — "
        "smoothed new cases and new deaths per million, hospital and ICU patients "
        "per million, and the share of people fully vaccinated. Describe how the "
        "waves came and went over those months, and whether deaths kept tracking "
        "cases as closely later in the year as they did earlier."},

    # ── l: UK, full year 2021 ~365 daily rows x 5 long columns ───────────────
    {"id": "L", "shape": "l", "text":
        "Go through the UK's whole of 2021 day by day — smoothed new cases and "
        "new deaths per million, hospital and ICU patients per million, and the "
        "share of people fully vaccinated. How many distinct waves were there "
        "across the year, when did each one crest, and how did the relationship "
        "between cases and deaths change as vaccination coverage grew?"},
]
