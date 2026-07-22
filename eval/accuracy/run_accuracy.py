#!/usr/bin/env python3
"""
Accuracy pilot runner: does result serialization change what the model gets right?

Each trial samples its own (country, 60-day window) stimulus from the pool in
tables.py and fetches it from the real BOSS server. Within a trial, both
serialisations carry the identical table and question, so the layout is the
only difference between conditions. Trials vary by rng seed. Writes one JSONL
record per call under results/.

Requirements:
    pip install anthropic

Usage:
  ./run_accuracy.py --dry-run          # build + print prompts, no API
  ./run_accuracy.py --n 2              # 2 formats x 3 task types x 2 trials = 12 calls
  ./run_accuracy.py                    # full experiment (2x3x20 = 120 calls)

Auth: ANTHROPIC_API_KEY in the environment (or an `ant auth login` profile).

Model: defaults to claude-haiku-4-5, override with --model.

The prompt names no format: both conditions get an identical
"Here is a data table: <served bytes> <question>" prompt. So the data layout itself
is the only cue.
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import tables
import tasks as tasklib
import score as scorelib

HERE = Path(__file__).resolve().parent
FORMATS = ("columnarjson", "rowrepjson")

SYSTEM = ("You answer questions about a data table. "
          "Respond with ONLY the requested JSON object - no prose, no code fences.")

# Models that reject sampling parameters (temperature would 400).
NO_SAMPLING_PREFIXES = ("claude-fable", "claude-mythos", "claude-opus-4-7",
                        "claude-opus-4-8", "claude-sonnet-5")

# Models capable of adaptive thinking (off by default; --thinking opts in).
# claude-sonnet-5 defaults to ON when omitted and thinking shares
# max_tokens with the answer.  Models not listed (e.g. claude-haiku-4-5)
# don't support thinking at all
THINKING_CAPABLE_PREFIXES = NO_SAMPLING_PREFIXES + ("claude-opus-4-6", "claude-sonnet-4-6")

MAX_TOKENS_DEFAULT = 256    # plenty for a bare {"answer": ...} JSON
MAX_TOKENS_THINKING = 4096  # provide headroom for reasoning + the answer


def build_prompt(served, question):
    return f"Here is a data table:\n\n{served}\n\n{question}"


def make_caller(model, thinking=False):
    import anthropic
    client = anthropic.Anthropic()
    kwargs = {}
    if not model.startswith(NO_SAMPLING_PREFIXES):
        kwargs["temperature"] = 0.0

    capable = model.startswith(THINKING_CAPABLE_PREFIXES)
    if thinking and not capable:
        print(f"  (note: {model} does not support thinking - running with it disabled)")
    enable_thinking = thinking and capable
    max_tokens = MAX_TOKENS_THINKING if enable_thinking else MAX_TOKENS_DEFAULT

    if capable:
        kwargs["thinking"] = ({"type": "adaptive", "display": "summarized"} if enable_thinking
                               else {"type": "disabled"})

    def call(prompt):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}], **kwargs)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        thinking = "\n".join(b.thinking for b in resp.content if b.type == "thinking")
        usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
        return text, thinking, usage

    return call


MAX_ATTEMPTS = 40  # per trial, across both rejection reasons below


def sample_trials(args, col, row):
    """(stimulus, task-instance) pairs; each trial gets its own window.

    col/row are the two persistent BOSS sessions from tables.sessions() - each
    new (country, window) costs two queries against them (one per result
    format)

    A candidate window is rejected into the trial list if:
      - it overlaps too much with a window already used for the same task
        (tables.overlaps_used)
      - the window has no eligible target for this task at all (e.g. no
        EXTREMUM with a clear margin)
    """
    rng = random.Random(args.seed)
    cache = {}
    trials = []
    total = len(args.tasks) * args.n
    for task in args.tasks:
        used = []  # windows already placed for THIS task
        for i in range(args.n):
            for _attempt in range(MAX_ATTEMPTS):
                key = tables.sample_window(rng, days=args.days)
                if tables.overlaps_used(key, used):
                    print(f"      -> {key[0]} {key[1]}..{key[2]} overlaps a "
                          f"window already used for {task}, resampling", flush=True)
                    continue
                if key not in cache:
                    print(f"  [{len(trials)}/{total}] fetching stimulus "
                          f"{len(cache) + 1}: {key[0]} {key[1]}..{key[2]}...",
                          end=" ", flush=True)
                    cache[key] = tables.build(col, row, *key)
                    print(f"{cache[key].table.nrows} rows", flush=True)
                stim = cache[key]
                try:
                    tr = tasklib.instantiate(task, stim.table, rng)
                except RuntimeError:
                    # the [trials placed/total] label above won't advance
                    # until an attempt succeeds.
                    print(f"      -> no eligible {task} target in that window, "
                          f"resampling", flush=True)
                    continue
                tr["window"] = {"code": key[0], "start": key[1], "end": key[2]}
                trials.append((stim, tr))
                used.append(key)
                break
            else:
                raise RuntimeError(
                    f"could not place a {task} trial in {MAX_ATTEMPTS} windows")
    return trials


def run(args):
    with tables.sessions() as (col, row):
        trials = sample_trials(args, col, row)
    sizes = [(len(s.columnar), len(s.rowrep)) for s, _ in trials]
    print(f"{len(trials)} trials over {len(set(id(s) for s, _ in trials))} stimuli | "
          f"columnar ~{sum(c for c, _ in sizes)//len(sizes):,}B / "
          f"rowrep ~{sum(r for _, r in sizes)//len(sizes):,}B per table")

    if args.dry_run:
        for stim, tr in trials[:2]:
            print(f"\n--- {tr['task']} @ {tr['window']} | expect {tr['expected']} "
                  f"| target {tr['target']}\n{tr['question']}")
        print(f"\ndry run: {len(trials)} trials x {len(FORMATS)} formats "
              f"= {len(trials) * len(FORMATS)} calls (none made)")
        return

    call = make_caller(args.model, thinking=args.thinking)
    outdir = HERE / "results"
    outdir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outfile = outdir / f"pilot_{stamp}.jsonl"

    done = 0
    total = len(trials) * len(FORMATS)
    with open(outfile, "w") as out:
        for i, (stim, tr) in enumerate(trials):
            for fmt in FORMATS:
                prompt = build_prompt(stim.served(fmt), tr["question"])
                text, thinking, usage = call(prompt)
                answer = scorelib.parse_answer(text)
                malformed = answer is None
                if malformed:  # one retry on unparseable output, then score as-is
                    text, thinking, u2 = call(prompt)
                    usage = {k: usage[k] + u2[k] for k in usage}
                    answer = scorelib.parse_answer(text)
                rec = {
                    "trial": i, "task": tr["task"], "format": fmt,
                    "model": args.model, "seed": args.seed,
                    "window": tr["window"], "nrows": stim.table.nrows,
                    "target": tr["target"], "expected": tr["expected"],
                    "raw": text, "thinking": thinking, "answer": answer,
                    "correct": scorelib.score(tr["task"], answer, tr["expected"]),
                    "malformed_first_try": malformed, "usage": usage,
                }
                out.write(json.dumps(rec) + "\n")
                out.flush()
                done += 1
                print(f"[{done}/{total}] {tr['task']:<9} {fmt:<13} "
                      f"{'OK ' if rec['correct'] else 'ERR'} "
                      f"answer={answer!r} expected={tr['expected']!r}")
                time.sleep(args.pause)
    print(f"\nwrote {outfile}")
    print(f"analyze: .venv/bin/python analyse_accuracy.py {outfile.name}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--model", default="claude-haiku-4-5")
    p.add_argument("--n", type=int, default=20, help="trials per task (per format)")
    p.add_argument("--thinking", action="store_true",
                   help="enable adaptive thinking (no-op on models that don't support it); "
                        f"bumps max_tokens to {MAX_TOKENS_THINKING}")
    p.add_argument("--tasks", default="lookup,extremum,aggregate",
                   type=lambda s: [t.strip() for t in s.split(",")])
    p.add_argument("--days", type=int, default=tables.WINDOW_DAYS,
                   help="stimulus window length in days (default: 60)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--pause", type=float, default=0.2,
                   help="seconds between calls (rate-limit headroom)")
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
