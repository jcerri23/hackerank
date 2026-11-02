"""SQLite-compatible SQL snapshot exports -> Observations.

Each .sql file is executed in an in-memory SQLite database.  Three table shapes exist:
  * generic replica tables  (resource_id, origin_system, collection_name, record_json, updated_at)
  * typed inventory tables  (customer_profiles, support_records, ... + snapshot_scope)
  * small typed primary/replica tables (customer_profiles_primary, search_documents_primary, ...)
Every row becomes one Observation; primary and replica copies of the same resource are
kept as separate observations (the `copy` field) so replication drift stays visible.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from ..models import Observation
from ..normalize import parse_ts
from .records import observe_record, scope_observation

HEADER_TS = re.compile(r"Captured at:\s*(\S+)")

TYPED_TABLE_COLLECTION = {
    "customer_profiles": "customer_profiles",
    "customer_search_documents": "customer_search_documents",
    "customer_analytics_profiles": "customer_analytics_profiles",
    "support_records": "support_records",
    "customer_exports": "customer_exports",
    "connector_subscriptions": "connector_subscriptions",
    "backup_restore_entries": "backup_restore_entries",
    "security_audit_records": "security_audit_records",
    "search_documents": "search_documents",
}


def _collection_for_table(table: str) -> str:
    for key, coll in TYPED_TABLE_COLLECTION.items():
        if table.startswith(key):
            return coll
    return table


def parse_sql(path: Path, evidence_id: str, captured_at: datetime) -> list[Observation]:
    text = path.read_text(encoding="utf-8")
    m = HEADER_TS.search(text)
    file_ts = parse_ts(m.group(1)) if m else captured_at
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(text)

    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    scope_ts: dict[str, datetime] = {}
    if "snapshot_scope" in tables:
        for row in db.execute("select origin_system, captured_at from snapshot_scope"):
            scope_ts[row["origin_system"]] = parse_ts(row["captured_at"]) or file_ts

    out: list[Observation] = []
    typed_scope: dict[str, set[str]] = {}
    for table in tables:
        if table == "snapshot_scope":
            continue
        cols = [c[1] for c in db.execute(f'pragma table_info("{table}")')]
        for row in db.execute(f'select * from "{table}"'):
            rec = dict(row)
            if "record_json" in cols:
                body = json.loads(rec["record_json"])
                body.setdefault("resource_id", rec["resource_id"])
                obs = observe_record(
                    body,
                    evidence_id=evidence_id,
                    observed_at=file_ts,
                    system_id=rec.get("origin_system"),
                    collection=rec.get("collection_name"),
                    note=f"sql:{table}",
                )
            else:
                rec.pop("updated_at", None)
                rec.pop("indexed_at", None)
                obs = observe_record(
                    rec,
                    evidence_id=evidence_id,
                    observed_at=file_ts,
                    collection=_collection_for_table(table),
                    note=f"sql:{table}",
                )
                if obs and obs.system_id in scope_ts:
                    obs.observed_at = scope_ts[obs.system_id]
            if obs:
                obs.copy = table
                out.append(obs)
                if "record_json" not in cols:
                    typed_scope.setdefault(obs.system_id, set()).add(obs.resource_id)
    db.close()
    # scoped absence proofs: a typed full-table capture (or an inventory whose
    # snapshot_scope lists the system) shows every record of that system at that instant
    # (inventories with a snapshot_scope table are curated subsets, not complete captures)
    if "snapshot_scope" not in tables:
        for system, rids in typed_scope.items():
            out.append(scope_observation(evidence_id, file_ts, system, rids))
    return out
