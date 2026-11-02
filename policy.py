"""Step 5 - Apply deletion_rules.csv to every relevant resource.

For each resource the policy decides the *required* end state (from its data category)
and compares it with the reconstructed state, producing at most one finding, one
remediation and one verification.  Nothing that policy requires to be retained is ever
scheduled for deletion; tombstones keep their stable user reference and only lose direct
identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import config as C
from .normalize import parse_ts
from .state import ResourceState

DELETE, REDACT, ANONYMIZE, RETAIN, DISABLE, BLOCK = "DELETE", "REDACT", "ANONYMIZE", "RETAIN", "DISABLE", "BLOCK"


@dataclass
class Decision:
    resource_id: str
    system_id: str
    category: str
    action: str
    finding: str | None
    remediation: str | None
    verification: str | None
    evidence_id: str
    violated: bool
    reason: str


class PolicyEngine:
    def __init__(self, rules: list[dict]):
        self.rules = {r["data_category"]: r for r in rules}

    def required_action(self, st: ResourceState, audit_at: datetime) -> str:
        cat = st.category
        if cat == C.DATA_FLOW:
            return DISABLE
        if cat == C.BACKUP_ARCHIVE:
            return BLOCK          # rule_14: retain the archive, block restore of the subject
        if cat == C.SECURITY_AUDIT_LOG:
            until = parse_ts(str(st.attrs.get("retention_until") or ""))
            return RETAIN if until is None or until >= audit_at else DELETE
        rule = self.rules.get(cat)
        return rule["required_action"] if rule else DELETE

    def decide(self, st: ResourceState, audit_at: datetime) -> Decision | None:
        action = self.required_action(st, audit_at)
        rid, sysid, cat, ev = st.resource_id, st.system_id, st.category, st.evidence_id
        mk = lambda f, r, v, viol, why: Decision(rid, sysid, cat, action, f, r, v, ev, viol, why)
        present = st.presence == "PRESENT"
        absent = st.presence == "ABSENT"

        if action == DISABLE:
            if st.enabled is True:
                return mk("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", True, "flow enabled")
            if st.enabled is False or absent:
                return mk(None, None, "VERIFY_DATA_FLOW_DISABLED", False, "flow disabled")
            return None
        if action == BLOCK:
            if st.restore_allowed is False:
                return mk(None, None, "VERIFY_RESTORE_BLOCKED", False, "restore blocked")
            if present or st.restore_allowed is True:
                return mk("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED", True, "restorable archive")
            return None
        if action == RETAIN:
            if present:
                return mk(None, None, "VERIFY_RECORD_PRESENT", False, "retained by policy")
            return None
        if action == DELETE:
            if present:
                return mk("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT", True, "record still exists")
            return mk(None, None, "VERIFY_RECORD_ABSENT", False, "record absent" if absent else "not observed")
        if action == REDACT:
            if present and st.pii:
                return mk("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED", True, "identifiers exposed")
            if present:
                return mk(None, None, "VERIFY_RECORD_REDACTED", False, "redacted")
            if absent:
                return mk(None, None, "VERIFY_RECORD_ABSENT", False, "record absent")
            return None
        if action == ANONYMIZE:
            if present and st.pii:
                return mk("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED", True, "still attributable")
            if present:
                return mk(None, None, "VERIFY_RECORD_ANONYMIZED", False, "anonymised")
            if absent:
                return mk(None, None, "VERIFY_RECORD_ABSENT", False, "record absent")
            return None
        return None
