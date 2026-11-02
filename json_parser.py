"""JSON evidence -> Observations.

Handled shapes (detected by keys, not by file name):
  * {service_exports: {system: {collection: [records]}}}   service resource exports
  * {snapshots: [{captured_at, data_store, collection, records}]}   snapshot bundles
  * {captured_at, collection, records}                     single-collection captures
  * {records: [{system, resource_id, record_type, result}]} lookup ledgers
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Observation
from ..normalize import is_null, parse_ts
from .lookup import observe_lookup
from .records import observe_record, scope_observation


def _ts(rec: dict, default: datetime) -> datetime:
    for key in ("observed_at", "recorded_at", "captured_at"):
        if rec.get(key):
            return parse_ts(rec[key]) or default
    return default


def _service_export(data: dict, evid: str, default_ts: datetime) -> list[Observation]:
    out = []
    for system, collections in data["service_exports"].items():
        for collection, records in collections.items():
            for rec in records:
                ts = _ts(rec, default_ts)
                body = {k: v for k, v in rec.items() if k != "observed_at"}
                if "status_code" in body:                        # profile lookups
                    present = str(body.get("status_code")) == "200" and body.get("profile")
                    profile = body.get("profile") or {}
                    rec2 = {"resource_id": body["resource_id"], **profile}
                    obs = observe_record(rec2, evidence_id=evid, observed_at=ts, system_id=system,
                                         collection="customer_profiles",
                                         presence="PRESENT" if present else "ABSENT",
                                         note=f"json:{collection}")
                elif "hits" in body:                              # search index queries
                    docs = (body.get("hits") or {}).get("documents") or []
                    doc = next((d for d in docs if d.get("resource_id") == body["resource_id"]), None)
                    rec2 = {"resource_id": body["resource_id"], **(doc or {})}
                    obs = observe_record(rec2, evidence_id=evid, observed_at=ts, system_id=system,
                                         collection="customer_search_documents",
                                         presence="PRESENT" if doc else "ABSENT",
                                         extra_tokens=None, note=f"json:{collection}")
                    if obs and body.get("query"):
                        from ..normalize import tokens_from_value
                        obs.identifiers = obs.identifiers | frozenset(tokens_from_value(body["query"]))
                else:
                    obs = observe_record(body, evidence_id=evid, observed_at=ts, system_id=system,
                                         collection=collection, note=f"json:{collection}")
                if obs:
                    out.append(obs)
    return out


def _flatten_search_doc(rec: dict) -> dict:
    if isinstance(rec.get("document"), dict):
        merged = {k: v for k, v in rec.items() if k != "document"}
        merged.update(rec["document"])
        return merged
    return rec


def _snapshots(data: dict, evid: str, default_ts: datetime) -> list[Observation]:
    out = []
    for snap in data["snapshots"]:
        ts = parse_ts(snap.get("captured_at")) or default_ts
        recs = snap.get("records", [])
        if snap.get("collection") != "operational_record_history" and snap.get("data_store"):
            out.append(scope_observation(evid, ts, snap["data_store"],
                                         {r.get("resource_id") for r in recs if r.get("resource_id")}))
        for rec in snap.get("records", []):
            if "result" in rec and "record_type" in rec:          # operational lookup history
                ref = rec.get("subject_reference") or rec.get("lookup_value")
                out.append(observe_lookup(
                    system=rec.get("system"), resource_id=rec["resource_id"],
                    record_type=rec.get("record_type"), result=rec.get("result", ""),
                    subject_reference=None if is_null(ref) else ref,
                    observed_at=_ts(rec, ts), evidence_id=evid, note="json:lookup-history"))
                continue
            obs = observe_record(_flatten_search_doc(rec), evidence_id=evid, observed_at=ts,
                                 system_id=snap.get("data_store"), collection=snap.get("collection"),
                                 note=f"json:{snap.get('collection')}")
            if obs:
                out.append(obs)
    return out


def _collection(data: dict, evid: str, default_ts: datetime) -> list[Observation]:
    ts = parse_ts(data.get("captured_at")) or default_ts
    out = []
    for rec in data["records"]:
        obs = observe_record(rec, evidence_id=evid, observed_at=ts, collection=data.get("collection"),
                             note=f"json:{data.get('collection')}")
        if obs:
            if "download_available" in rec and rec["download_available"] is False:
                obs.attrs["download_available"] = False
            out.append(obs)
    return out


def _lookup_ledger(data: dict, evid: str, default_ts: datetime) -> list[Observation]:
    out = []
    for rec in data["records"]:
        ref = rec.get("subject_reference") or rec.get("lookup_value")
        out.append(observe_lookup(
            system=rec.get("system"), resource_id=rec["resource_id"], record_type=rec.get("record_type"),
            result=rec.get("result", ""), subject_reference=None if is_null(ref) else ref,
            observed_at=_ts(rec, default_ts), evidence_id=evid, note="json:lookup"))
    return out


def parse_json(path: Path, evidence_id: str, captured_at: datetime) -> list[Observation]:
    data = json.loads(path.read_text(encoding="utf-8"))
    default_ts = parse_ts(data.get("exported_at") or data.get("captured_at")) or captured_at
    if "service_exports" in data:
        return _service_export(data, evidence_id, default_ts)
    if "snapshots" in data:
        return _snapshots(data, evidence_id, default_ts)
    records = data.get("records") or []
    if records and "result" in records[0] and "system" in records[0]:
        return _lookup_ledger(data, evidence_id, default_ts)
    if "records" in data:
        return _collection(data, evidence_id, default_ts)
    return []
