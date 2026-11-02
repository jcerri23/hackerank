"""Log files -> Observations.

Seven line layouts exist (key=value, pipe-separated, bracketed, syslog, cloud-runtime with
an escaped `message="..."` payload, JSON-per-line and record-lookup rows).  Each line is
reduced to (timestamp, service, trace_id, event, fields).  Lines are then grouped by trace:
the lookup key of a `*_started` event (an email, phone, hash or user id) is attached to the
resource returned by the `*_completed` event of the same trace.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .. import config as C
from ..models import Observation
from ..normalize import is_null, parse_ts, tokens_from_value
from .lookup import observe_lookup
from .records import as_bool

KV_RE = re.compile(r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|(\S+))')
BRACKET_RE = re.compile(r"^\[(\w+)\]\s+\[([\w\-]+)\]\s+\[([\w\-]+)\]\s+(\w+):\s*(.*)$")
SYSLOG_RE = re.compile(r"^<\d+>\d+\s+\S+\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")
LOOKUP_KEYS = ("lookup_key", "match_key", "subject_key", "subject_ref", "correlation_key",
               "owner_key", "scan_key")


def _kv(text: str) -> dict:
    out = {}
    for m in KV_RE.finditer(text):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else m.group(3)
        out[key] = val.replace('\\"', '"') if val is not None else val
    return out


def parse_line(line: str):
    line = line.strip()
    if not line:
        return None
    ts_txt, _, rest = line.partition(" ")
    ts = parse_ts(ts_txt)
    if ts is None:
        return None
    rest = rest.strip()
    if rest.startswith("{"):
        try:
            fields = json.loads(rest)
        except json.JSONDecodeError:
            return None
        service = fields.get("service") or fields.get("logger")
        trace = fields.get("trace_id")
        event = fields.get("event")
    elif rest.startswith("|"):
        parts = [p.strip() for p in rest.strip("|").split("|")]
        # | LEVEL | service | trace | event | k="v" ...
        if len(parts) < 4:
            return None
        _, service, trace, event = parts[:4]
        fields = _kv(" ".join(parts[4:]))
    elif rest.startswith("["):
        m = BRACKET_RE.match(rest)
        if not m:
            return None
        _, service, trace, event, tail = m.groups()
        fields = _kv(tail)
    elif rest.startswith("<"):
        m = SYSLOG_RE.match(rest)
        if not m:
            return None
        service, trace, tail = m.groups()
        fields = _kv(tail)
        event = fields.get("event")
    else:
        fields = _kv(rest)
        if "message" in fields and "=" in (fields["message"] or ""):
            inner = _kv(fields["message"])
            fields.update(inner)
        service = fields.get("service") or fields.get("function")
        trace = fields.get("trace_id") or fields.get("requestId")
        event = fields.get("event")
    fields = {k: v for k, v in fields.items() if k not in ("message",)}
    return ts, service, trace, event, fields


def _presence_from_event(event: str, f: dict):
    """Return (presence, pii, attrs, category) for one completed-read event."""
    attrs: dict = {}
    pii = None
    presence = "PRESENT"
    category = None
    if event in ("profile_lookup_completed", "contact_query_completed", "contact_lookup_completed",
                 "support_ticket_read_completed", "audit_record_read_completed"):
        rows = f.get("rows")
        status = f.get("http_status")
        if rows == "0" or status == "404":
            presence = "ABSENT"
    if event == "index_query_completed":
        presence = "ABSENT" if (f.get("result") or "").lower() in ("miss", "none", "empty") or f.get("total_hits") == "0" else "PRESENT"
    if event in ("analytics_profile_read", "analytics_profile_read_completed"):
        di = f.get("direct_identifiers")
        if di is not None:
            pii = di != "0"
        else:
            pii = any(not is_null(f.get(k)) for k in ("email", "phone", "email_hash"))
    if event in ("analytics_identity_scan", "message_pii_scan", "attachment_pii_scan"):
        pii = True
    if event == "attachment_pii_scan":
        category = C.SUPPORT_ATTACHMENT
    if event == "contact_lookup_completed":
        category = C.MARKETING_PROFILE
        pii = presence == "PRESENT"
    if event in ("contact_query_completed", "deletion_marker_check"):
        category = C.SECONDARY_IDENTIFIER   # legacy contact index entry, not a conversation
    if event == "deletion_marker_check":
        if (f.get("marker_found") or "").lower() == "false":
            presence, pii = "PRESENT", True
        else:
            presence = "ABSENT"
    if event in ("profile_lookup_completed", "support_ticket_read_completed") and presence == "PRESENT":
        keys = ("email", "phone", "requester_email", "requester_phone", "requester_name", "full_name")
        if any(k in f for k in keys):
            pii = any(not is_null(f.get(k)) for k in keys)
    if event == "message_read":
        txt = f.get("message_text") or ""
        pii = not ("REDACTED" in txt.upper() or is_null(txt)) and bool(tokens_from_value(txt))
    if event in ("connector_config_read_completed", "connector_heartbeat", "connector_config_loaded"):
        en = as_bool(f.get("enabled"))
        if en is not None:
            attrs["enabled"] = en
        category = C.DATA_FLOW
    if event in ("restore_policy_read_completed",):
        ra = as_bool(f.get("restore_allowed"))
        if ra is not None:
            attrs["restore_allowed"] = ra
        if (f.get("archive_present") or "").lower() == "false":
            presence = "ABSENT"
    if event == "restore_candidate_evaluated":
        eligible = as_bool(f.get("eligible"))
        block = as_bool(f.get("deletion_block"))
        attrs["restore_allowed"] = bool(eligible) and not bool(block)
    if event == "restore_authorization_completed":
        attrs["restore_allowed"] = (f.get("decision") or "").lower() in ("allow", "allowed", "approve", "approved")
    if event in ("export_download_check_completed", "export_metadata_read_completed"):
        av = as_bool(f.get("available"))
        attrs["download_available"] = av
        if av is False:
            presence = "ABSENT"
    if event == "retained_order_loaded":
        category = C.LEGAL_HOLD_RECORD
    for k in ("retention_until", "event_code", "connector_name", "last_delivery_status", "index"):
        if k in f and not is_null(f[k]):
            attrs[k] = f[k]
    return presence, pii, attrs, category


COMPLETED_EVENTS = {
    "profile_lookup_completed", "index_query_completed", "analytics_profile_read",
    "analytics_profile_read_completed", "analytics_identity_scan", "message_pii_scan",
    "attachment_pii_scan", "support_ticket_read_completed", "audit_record_read_completed",
    "connector_config_read_completed", "connector_heartbeat", "connector_config_loaded",
    "restore_policy_read_completed", "restore_candidate_evaluated", "restore_authorization_completed",
    "export_download_check_completed", "export_metadata_read_completed", "contact_query_completed",
    "contact_lookup_completed", "deletion_marker_check", "retained_order_loaded", "message_read",
}


def parse_log(path: Path, evidence_id: str, captured_at: datetime) -> list[Observation]:
    parsed = [p for p in (parse_line(l) for l in path.read_text(encoding="utf-8").splitlines()) if p]
    trace_keys: dict[str, set[str]] = defaultdict(set)
    for ts, service, trace, event, f in parsed:
        for k in LOOKUP_KEYS:
            if trace and k in f and not is_null(f[k]):
                trace_keys[trace] |= tokens_from_value(f[k])

    out: list[Observation] = []
    for ts, service, trace, event, f in parsed:
        if event == "record_lookup" and f.get("resource_id"):
            out.append(observe_lookup(system=f.get("system"), resource_id=f["resource_id"],
                                      record_type=f.get("record_type"), result=f.get("result", ""),
                                      subject_reference=f.get("subject_reference"), observed_at=ts,
                                      evidence_id=evidence_id, note="log:record_lookup"))
            continue
        rid = f.get("resource_id")
        if not rid or event not in COMPLETED_EVENTS:
            continue
        system = C.system_for_resource(rid)
        if not system:
            continue
        presence, pii, attrs, category = _presence_from_event(event, f)
        tokens: set[str] = set()
        for k, v in f.items():
            if k in LOOKUP_KEYS or k in ("email", "phone", "email_hash", "matched_value", "customer_email",
                                         "customer_reference", "subject_reference", "owner_email",
                                         "requester_email", "requester_phone"):
                tokens |= tokens_from_value(v)
        if trace:
            tokens |= trace_keys.get(trace, set())
        out.append(Observation(
            evidence_id=evidence_id, observed_at=ts, resource_id=rid, system_id=system,
            presence=presence, pii=pii, identifiers=frozenset(tokens),
            category=category or C.SYSTEM_DEFAULT_CATEGORY.get(system), attrs=attrs,
            note=f"log:{event}",
        ))
    return out
