"""Loading of the participant-facing CSV files under dataset/."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .config import DATASET_DIR
from .models import Request
from .normalize import parse_ts


def read_csv(name: str, base: Path = DATASET_DIR) -> list[dict]:
    with open(base / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_requests(name: str = "deletion_requests.csv") -> list[Request]:
    out = []
    for r in read_csv(name):
        out.append(
            Request(
                case_id=r["case_id"],
                email=r["email"],
                requested_at=parse_ts(r["requested_at"]),
                reported_status=r["reported_status"],
                reported_at=parse_ts(r["reported_at"]),
                audit_at=parse_ts(r["audit_at"]),
                request_text=r["request_text"],
            )
        )
    return out


@dataclass
class Dataset:
    identities: list[dict]
    audit_log: list[dict]
    activity: list[dict]
    rules: list[dict]
    manifest: list[dict]


def load_dataset() -> Dataset:
    return Dataset(
        identities=read_csv("user_identities.csv"),
        audit_log=read_csv("user_audit_log.csv"),
        activity=read_csv("system_activity.csv"),
        rules=read_csv("deletion_rules.csv"),
        manifest=read_csv("evidence_files.csv"),
    )
