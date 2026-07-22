"""
Score the accuracy experiment: parse the model's answer and compare it to the
ground truth expected answer.

The model is instructed to answer with ONLY `{"answer": ...}`; parse_answer
tolerates code fences and stray prose around the object. A trial whose answer
can't be parsed scores wrong and is flagged malformed

Numeric match: relative tolerance REL_TOL (matches tasks.py). EXTREMUM needs
the date exact AND the value within tolerance. AGGREGATE is a plain number,
scored the same way as LOOKUP.
"""

import json
import re

REL_TOL = 0.005


def parse_answer(text):
    """Extract the {"answer": ...} object; returns the answer value or None."""
    if not text:
        return None
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "answer" not in obj:
        return None
    return obj["answer"]


def _num_match(got, expected):
    try:
        got = float(got)
    except (TypeError, ValueError):
        return False
    return abs(got - expected) <= REL_TOL * max(abs(expected), 1e-9)


def score(task, answer, expected):
    """True iff the parsed answer matches ground truth for this task type."""
    if answer is None:
        return False
    if task == "lookup" or task == "aggregate":
        return _num_match(answer, expected)
    if task == "extremum":
        if not isinstance(answer, dict):
            return False
        return (str(answer.get("date", "")).strip() == expected["date"]
                and _num_match(answer.get("value"), expected["value"]))
    raise ValueError(f"unknown task: {task}")
