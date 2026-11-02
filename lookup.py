"""Lookup-result rows: `system | resource_id | record_type | result | subject_reference`.

The same tabular shape appears in shift handovers (.txt), record-lookup logs and several
JSON ledgers; this module is the single interpreter for those result codes.
"""

from __future__ import annotations

from datetime import datetime

from .. import config as C
from ..models import Observation
from ..normalize import tokens_from_value


def observe_lookup(
    *,
    system: str,
    resource_id: str,
    record_type: str | None,
    result: str,
    subject_reference: str | None,
    observed_at: datetime,
    evidence_id: str,
    note: str = "",
) -> Observation:
    result = (result or "").strip().upper()
    tokens = tokens_from_value(subject_reference) if subject_reference else set()
    presence, pii, attrs = "PRESENT", None, {}
    if result in ("NOT_FOUND", "ABSENT", "DELETED", "MISS"):
        presence = "ABSENT"
    elif result == "IDENTIFIABLE":
        pii = True
    elif result == "ANONYMIZED":
        pii = False
        attrs["anonymized"] = True
    elif result == "REDACTED":
        pii = False
        attrs["redacted"] = True
    elif result == "ACTIVE":
        attrs["enabled"] = True
        attrs["restore_allowed"] = True
    elif result in ("DISABLED", "STOPPED"):
        attrs["enabled"] = False
    elif result == "BLOCKED":
        attrs["restore_allowed"] = False
    elif result == "FOUND":
        # a FOUND row that names the subject by email still exposes it
        pii = any(t.startswith("email:") for t in tokens) or None
    category = record_type if record_type and record_type != "DATA_FLOW" else None
    if record_type == "DATA_FLOW":
        category = C.DATA_FLOW
    return Observation(
        evidence_id=evidence_id,
        observed_at=observed_at,
        resource_id=resource_id,
        system_id=system or C.system_for_resource(resource_id) or "",
        presence=presence,
        pii=pii,
        identifiers=frozenset(tokens),
        category=category,
        attrs=attrs,
        note=note or f"lookup:{result}",
    )
