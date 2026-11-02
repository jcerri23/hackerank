"""Serialise results to output.csv and validate them against the output contract."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import config as C
from .models import CaseResult


def case_sort_key(case_id: str):
    num = "".join(ch for ch in case_id if ch.isdigit())
    return (int(num) if num else 0, case_id)


def steps_json(result: CaseResult) -> str:
    return json.dumps([s.as_dict(i) for i, s in enumerate(result.steps, 1)], separators=(",", ":"), sort_keys=True)


def validate(results: list[CaseResult]) -> list[str]:
    errors = []
    for r in results:
        if r.deletion_status not in (C.COMPLETE, C.INCOMPLETE, C.NOT_APPLICABLE):
            errors.append(f"{r.case_id}: bad status {r.deletion_status}")
        if r.deletion_status == C.NOT_APPLICABLE and r.steps:
            errors.append(f"{r.case_id}: NOT_APPLICABLE must have no steps")
        last = (-1, -1)
        for s in r.steps:
            if C.OP_PHASE.get(s.operation) != s.phase or C.OP_STATE.get(s.operation) != s.state:
                errors.append(f"{r.case_id}: inconsistent step {s}")
            if r.deletion_status == C.COMPLETE and s.phase != C.VERIFICATION:
                errors.append(f"{r.case_id}: COMPLETE with non-verification step")
            if r.deletion_status == C.INCOMPLETE and s.phase != C.FINDING and s.evidence_id:
                errors.append(f"{r.case_id}: future step must not cite evidence")
            key = (C.PHASE_ORDER[s.phase], C.OP_ORDER[s.operation])
            if key < last:
                errors.append(f"{r.case_id}: step order violated")
            last = key
    return errors


def write_output(results: list[CaseResult], path: Path = C.OUTPUT_PATH) -> None:
    results = sorted(results, key=lambda r: case_sort_key(r.case_id))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in results:
            w.writerow([r.case_id, r.deletion_status, steps_json(r)])
