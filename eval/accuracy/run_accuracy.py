#!/usr/bin/env python3
"""
Accuracy evaluation across multiple formats
Each trial samples its own (country, 60-day window) fixture from the pool in
tables.py and fetches it from the real BOSS server once per format under test.
Trials vary by rng seed. Writes one JSONL record per call under results/.

Requirements:
    pip install anthropic

Usage:
  ./run_accuracy.py             # full run: 5 formats x 3 tasks x 20 trials
  ./run_accuracy.py --n 2       # 5 formats x 3 tasks x 2 trials
  ./run_accuracy.py --formats columnarjson auto    # only those two formats (2 formats x 3 tasks x 20 trials)
  ./run_accuracy.py --n 5 --tasks lookup aggregate # 5 formats x 2 tasks x 5 trials

Auth: ANTHROPIC_API_KEY in the environment

Defaults:
  model: claude-sonnet-5
  thinking: off
  days: 60
"""

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import tables
import tasks as tasklib
import score as scorelib

HERE = Path(__file__).resolve().parent

SYSTEM = ("You answer questions about a data table. "
          "Respond with ONLY the requested JSON object - no prose, no code fences.")

# Sonnet 5 and Opus 5 reject sampling parameters
# Haiku 4.5 does not support adaptive thinking
MODELS = {
    "claude-haiku-4-5": {"sampling": True,  "thinking": False},
    "claude-sonnet-5":  {"sampling": False, "thinking": True},
    "claude-opus-5":    {"sampling": False, "thinking": True},
}

# Headroom for reasoning and avoid answer truncation
MAX_TOKENS_DEFAULT = 1024
MAX_TOKENS_THINKING = 4096

# for placing a trial in a non-overlapping window
MAX_ATTEMPTS = 40 


def _resampling(reason):
    print(f"      -> {reason}, resampling", flush=True)


def place_trial(task, days, procs, rng, used, progress):
    """Generate one (fixture, task-instance) pair, appending the window to
    `used`. A candidate window is rejected (and resampled, up to MAX_ATTEMPTS
    times) if it overlaps too much with a window already used for this task,
    or has no eligible target for the task."""
    for _attempt in range(MAX_ATTEMPTS):
        key = tables.sample_window(rng, days=days)
        code, start, end = key
        window_label = f"{code} {start}..{end}"
        if tables.overlaps_used(key, used):
            _resampling(f"{window_label} overlaps a window already used for {task}")
            continue
        print(f"  [{progress}] fetching {window_label}...", end=" ", flush=True)
        fixture = tables.build(procs, *key, task=task)
        print(f"{fixture.table.nrows} rows", flush=True)
        try:
            task_instance = tasklib.instantiate(task, fixture.table, rng)
        except RuntimeError:
            _resampling(f"no eligible {task} target in {window_label}")
            continue
        task_instance["window"] = {"code": code, "start": start, "end": end}
        used.append(key)
        return fixture, task_instance
    raise RuntimeError(f"could not place a {task} trial in {MAX_ATTEMPTS} windows")


def sample_trials(args, procs):
    """Sample args.n trials for each task, returning a list of
    (fixture, task-instance) pairs."""
    rng = random.Random(args.seed)
    trials = []
    total = len(args.tasks) * args.n
    for task in args.tasks:
        used = []
        for _ in range(args.n):
            trials.append(place_trial(task, args.days, procs, rng, used,
                                      f"{len(trials)}/{total}"))
    return trials


def build_prompt(served, question):
    return f"Here is a data table:\n\n{served}\n\n{question}"


def make_caller(model, thinking=False):
    import anthropic
    client = anthropic.Anthropic()
    caps = MODELS[model]
    kwargs = {}
    if caps["sampling"]:
        kwargs["temperature"] = 0.0

    capable = caps["thinking"]
    if thinking and not capable:
        print(f"  (note: {model} does not support thinking - running with it disabled)")
    thinking_enabled = thinking and capable
    max_tokens = MAX_TOKENS_THINKING if thinking_enabled else MAX_TOKENS_DEFAULT

    if capable:
        kwargs["thinking"] = ({"type": "adaptive", "display": "summarized"} if thinking_enabled
                               else {"type": "disabled"})

    def call(prompt):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}], **kwargs)
        text = next((block.text for block in resp.content if block.type == "text"), "")
        thinking = "\n".join(block.thinking for block in resp.content if block.type == "thinking")
        usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
        return text, thinking, usage

    return call


def ask(call, prompt):
    """One model call, with one retry on unparseable output (then scored
    as-is). Usage is summed across both attempts."""
    text, thinking, usage = call(prompt)
    answer = scorelib.parse_answer(text)
    malformed = answer is None
    if malformed:
        text, thinking, u2 = call(prompt)
        usage = {k: usage[k] + u2[k] for k in usage}
        answer = scorelib.parse_answer(text)
    return text, thinking, answer, malformed, usage


def run(args):
    with tables.sessions(args.formats) as procs:
        trials = sample_trials(args, procs)

        if args.dry_run:
            for fixture, task_instance in trials[:2]:
                print(f"\n--- {task_instance['task']} @ {task_instance['window']} "
                      f"| expect {task_instance['expected']} "
                      f"| target {task_instance['target']}\n{task_instance['question']}")
            print(f"\ndry run: {len(trials)} trials x {len(args.formats)} formats "
                  f"= {len(trials) * len(args.formats)} calls (none made)")
            return

        call = make_caller(args.model, thinking=args.thinking)
        outdir = HERE / "results"
        outdir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        outfile = outdir / f"acc_result_{stamp}.jsonl"

        done = 0
        total = len(trials) * len(args.formats)
        with open(outfile, "w") as out:
            for i, (fixture, task_instance) in enumerate(trials):
                for fmt in args.formats:
                    resolved = fixture.resolved[fmt]
                    prompt = build_prompt(fixture.served[fmt], task_instance["question"])
                    text, thinking, answer, malformed, usage = ask(call, prompt)
                    rec = {
                        "trial": i,
                        "task": task_instance["task"],
                        "format": fmt,
                        "resolved_format": resolved,
                        "model": args.model,
                        "seed": args.seed,
                        "window": task_instance["window"],
                        "nrows": fixture.table.nrows,
                        "target": task_instance["target"],
                        "expected": task_instance["expected"],
                        "raw": text,
                        "thinking": thinking,
                        "answer": answer,
                        "correct": scorelib.score(task_instance["task"], answer, task_instance["expected"]),
                        "malformed_first_try": malformed,
                        "usage": usage,
                    }
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    done += 1
                    resolved_note = f" -> {resolved}" if fmt == tables.AUTO_FORMAT else ""
                    print(f"[{done}/{total}] {task_instance['task']:<9} {fmt:<20}{resolved_note:<14} "
                          f"{'OK ' if rec['correct'] else 'ERR'} "
                          f"answer={answer!r} expected={task_instance['expected']!r}")
                    if done < total:
                        time.sleep(args.pause)
    print(f"\nwrote {outfile}")
    print(f"analyze: .venv/bin/python analyse_accuracy.py {outfile.name}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--model", default="claude-sonnet-5", choices=MODELS,
                   help="model under test (default: claude-sonnet-5)")
    p.add_argument("--n", type=int, default=20,
                   help="trials per task (per format)")
    p.add_argument("--thinking", action="store_true",
                   help="enable adaptive thinking (ignored on unsupported models)")
    p.add_argument("--tasks", nargs="+", choices=tasklib.TASKS, default=list(tasklib.TASKS),
                   metavar="TASK", help=f"one or more of {', '.join(tasklib.TASKS)} (default: all)")
    p.add_argument("--formats", nargs="+", choices=(*tables.FORMATS, tables.AUTO_FORMAT),
                   default=list(tables.FORMATS), metavar="FORMAT",
                   help=f"one or more of {', '.join((*tables.FORMATS, tables.AUTO_FORMAT))} "
                        "(default: all but auto)")
    p.add_argument("--days", type=int, default=tables.WINDOW_DAYS,
                   help=f"fixture window length in days (default: {tables.WINDOW_DAYS})")
    p.add_argument("--seed", type=int, default=7,
                   help="rng seed")
    p.add_argument("--pause", type=float, default=0.2,
                   help="seconds between calls")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    run(args)


if __name__ == "__main__":
    main()
