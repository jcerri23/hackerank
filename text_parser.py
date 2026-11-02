"""Plain-text records (shift handovers): `- ts | system | resource | category | result | ref`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..models import Observation
from ..normalize import parse_ts
from .lookup import observe_lookup


def parse_text(path: Path, evidence_id: str, captured_at: datetime) -> list[Observation]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.lstrip("- ").split("|")]
        if len(parts) != 6:
            continue
        ts, system, rid, category, result, ref = parts
        out.append(observe_lookup(
            system=system, resource_id=rid, record_type=category, result=result,
            subject_reference=None if ref.lower().startswith("no ") else ref,
            observed_at=parse_ts(ts) or captured_at, evidence_id=evidence_id, note="text:handover"))
    return out
