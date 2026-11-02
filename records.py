"""Shared logic that turns one structured record (row, JSON object, log event) into an
Observation.  Every format-specific parser funnels through `observe_record`."""

from __future__ import annotations

from datetime import datetime

from .. import config as C
from ..models import Observation
from ..normalize import EMAIL_RE, PHONE_RE, RESOURCE_RE, is_null, tokens_from_fields, tokens_from_value

# fields whose non-null value means a direct identifier is still stored in the record
DIRECT_FIELDS = {
    "full_name", "name", "display_name", "requester_name", "customer_name",
    "email", "requester_email", "owner_email", "customer_email", "uploaded_by",
    "phone", "requester_phone", "owner_phone", "customer_phone",
    "email_hash", "matched_value",
}
# free-text fields that may embed identifiers
TEXT_FIELDS = {"message_text", "message_excerpt", "body", "note"}
# fields that only reference the subject (allowed in redacted / tombstone records)
REFERENCE_FIELDS = {"customer_reference", "subject_reference", "user_id", "subject_selector",
                    "lookup_key", "correlation_key", "subject_ref", "subject_key", "match_key"}

ATTR_FIELDS = {
    "enabled", "restore_allowed", "retention_until", "retained_until", "download_available",
    "account_status", "connector_name", "mode", "destination", "file_name", "storage_class",
    "event_code", "event_name", "ticket_id", "index_name", "audience_cohort", "archive_key",
    "archive_path", "expires_at", "last_delivery_status", "source_resource_id", "order_reference",
    "retention_reason", "scheduler_registered", "eligible", "deletion_block", "object_key",
}

COLLECTION_CATEGORY = {
    "customer_profiles": C.USER_PROFILE,
    "customer_search_documents": C.SEARCH_INDEX_ENTRY,
    "search_documents": C.SEARCH_INDEX_ENTRY,
    "customer_analytics_reports": C.ANALYTICS_PROFILE,
    "customer_analytics_profiles": C.ANALYTICS_PROFILE,
    "support_conversations": C.SUPPORT_MESSAGE,
    "support_records": C.SUPPORT_MESSAGE,
    "tickets": C.SUPPORT_MESSAGE,
    "ticket_attachments": C.SUPPORT_ATTACHMENT,
    "customer_export_jobs": C.USER_EXPORT,
    "customer_exports": C.USER_EXPORT,
    "exports": C.USER_EXPORT,
    "connector_configurations": C.DATA_FLOW,
    "connector_subscriptions": C.DATA_FLOW,
    "connectors": C.DATA_FLOW,
    "backup_catalog": C.BACKUP_ARCHIVE,
    "backup_restore_entries": C.BACKUP_ARCHIVE,
    "restore_entries": C.BACKUP_ARCHIVE,
    "security_audit_events": C.SECURITY_AUDIT_LOG,
    "security_audit_records": C.SECURITY_AUDIT_LOG,
    "audit_records": C.SECURITY_AUDIT_LOG,
    "deleted_customer_references": C.ACCOUNT_TOMBSTONE,
    "retained_order_records": C.LEGAL_HOLD_RECORD,
}


def category_for(collection: str | None, system: str | None) -> str | None:
    if collection:
        for key, cat in COLLECTION_CATEGORY.items():
            if key in collection:
                return cat
    return C.SYSTEM_DEFAULT_CATEGORY.get(system or "")


def has_direct_pii(record: dict) -> bool:
    for k, v in record.items():
        if isinstance(v, dict):
            if has_direct_pii(v):
                return True
            continue
        if is_null(v):
            continue
        if k in DIRECT_FIELDS:
            return True
        if k in TEXT_FIELDS and (EMAIL_RE.search(str(v)) or PHONE_RE.search(str(v))):
            return True
        if k in ("subject_reference", "subject_selector") and EMAIL_RE.search(str(v)):
            return True
    return False


def as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "enabled", "active"):
        return True
    if s in ("0", "false", "no", "disabled", "stopped"):
        return False
    return None


def observe_record(
    record: dict,
    *,
    evidence_id: str,
    observed_at: datetime,
    resource_id: str | None = None,
    system_id: str | None = None,
    collection: str | None = None,
    presence: str | None = "PRESENT",
    extra_tokens: set[str] | None = None,
    weight: int = 2,
    kind: str = "file",
    note: str = "",
) -> Observation | None:
    rid = resource_id or record.get("resource_id")
    if not rid or is_null(rid):
        return None
    rid = str(rid)
    system = system_id or C.system_for_resource(rid)
    if not system:
        return None
    tokens = tokens_from_fields(record)
    if extra_tokens:
        tokens |= extra_tokens
    attrs = {}
    for k, v in record.items():
        if k in ATTR_FIELDS and not is_null(v):
            attrs[k] = v
    links = set()
    for k, v in record.items():
        if isinstance(v, str) and k != "resource_id":
            for m in RESOURCE_RE.findall(v):
                if m != rid:
                    links.add(m)
    pii = has_direct_pii(record) if presence == "PRESENT" else None
    return Observation(
        evidence_id=evidence_id,
        observed_at=observed_at,
        resource_id=rid,
        system_id=system,
        presence=presence,
        pii=pii,
        identifiers=frozenset(tokens),
        category=category_for(collection, system),
        attrs=attrs,
        links=frozenset(links),
        kind=kind,
        weight=weight,
        note=note,
    )


def tokens_of(value) -> set[str]:
    return tokens_from_value(value)


def scope_observation(evidence_id: str, observed_at: datetime, system: str, present: set[str]) -> Observation:
    """A complete capture of one system: every resource of that system not listed was absent."""
    return Observation(evidence_id=evidence_id, observed_at=observed_at, resource_id=f"scope:{system}",
                       system_id=system, presence=None, attrs={"present": frozenset(present)},
                       weight=1, note="scope")
