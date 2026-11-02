"""Compare predictions with expected answers (sample_output.csv by default).

    python3 code/evaluation/main.py                       # sample predictions vs sample_output
    python3 code/evaluation/main.py --pred X.csv --gold Y.csv [--verbose]

Metrics
  * status accuracy     - deletion_status exact match
  * step precision/recall/F1 - a step matches when (phase, system_id, resource_id,
                          operation, state, evidence_id) are all equal (order ignored)
  * field accuracy      - per field, over gold steps aligned to predicted steps on
                          (phase, system_id, resource_id)
  * sequence accuracy   - steps_json exactly equal (order included)
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def load(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"] or "[]")) for r in csv.DictReader(fh)}


def key(s: dict) -> tuple:
    return tuple(s.get(f, "") for f in FIELDS)


def evaluate(pred: dict, gold: dict, verbose: bool = False) -> dict:
    status_ok = tp = fp = fn = seq_ok = 0
    field_hits, field_total = Counter(), 0
    failures = []
    for cid, (gstatus, gsteps) in gold.items():
        pstatus, psteps = pred.get(cid, ("MISSING", []))
        status_ok += pstatus == gstatus
        gk, pk = Counter(map(key, gsteps)), Counter(map(key, psteps))
        inter = sum((gk & pk).values())
        tp += inter
        fp += sum(pk.values()) - inter
        fn += sum(gk.values()) - inter
        seq = [key(s) for s in gsteps] == [key(s) for s in psteps]
        seq_ok += seq
        align = {(s["phase"], s["system_id"], s["resource_id"]): s for s in psteps}
        for s in gsteps:
            field_total += 1
            p = align.get((s["phase"], s["system_id"], s["resource_id"]))
            for f in FIELDS:
                field_hits[f] += bool(p) and p.get(f) == s.get(f)
        if not seq or pstatus != gstatus:
            failures.append((cid, gstatus, pstatus, sorted(gk - pk), sorted(pk - gk)))
    n = len(gold)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    res = {
        "cases": n,
        "status_accuracy": status_ok / n if n else 0,
        "step_precision": prec,
        "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "sequence_accuracy": seq_ok / n if n else 0,
        "field_accuracy": {f: field_hits[f] / field_total for f in FIELDS} if field_total else {},
        "failures": failures,
    }
    if verbose:
        for cid, g, p, missing, extra in failures:
            print(f"--- {cid}: gold={g} pred={p}")
            for m in missing:
                print("    missing:", m)
            for e in extra:
                print("    extra:  ", e)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", type=Path, default=ROOT / "code" / "evaluation" / "sample_predictions.csv")
    ap.add_argument("--gold", type=Path, default=ROOT / "dataset" / "sample_output.csv")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)
    res = evaluate(load(args.pred), load(args.gold), args.verbose)
    print(f"cases               {res['cases']}")
    print(f"status accuracy     {res['status_accuracy']:.3f}")
    print(f"step precision      {res['step_precision']:.3f}")
    print(f"step recall         {res['step_recall']:.3f}")
    print(f"step F1             {res['step_f1']:.3f}")
    print(f"sequence accuracy   {res['sequence_accuracy']:.3f}")
    for f, v in res["field_accuracy"].items():
        print(f"field {f:14} {v:.3f}")


if __name__ == "__main__":
    main()
