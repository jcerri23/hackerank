"""Plain data classes shared by every pipeline step."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Request:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass
class Observation:
    """One grounded fact about one resource at one moment, extracted from evidence.

    Every parser (SQL, JSON, logs, text, PDF, OCR, activity stream) emits these, so the
    rest of the pipeline never needs to know which file format a fact came from.
    """

    evidence_id: str                 # file_id, activity_id or audit_id that proves it
    observed_at: datetime
    resource_id: str
    system_id: str
    presence: str | None = None      # PRESENT / ABSENT / None (unknown)
    pii: bool | None = None          # direct identifiers still in the record?
    identifiers: frozenset[str] = frozenset()   # normalised tokens: email:, phone:, hash:, user:
    category: str | None = None      # data category when the evidence states it
    attrs: dict = field(default_factory=dict)  # enabled, restore_allowed, retention_until, ...
    links: frozenset[str] = frozenset()         # other resource ids this record points at
    kind: str = "file"               # file | activity | audit
    weight: int = 2                  # 2 = direct read of the system, 1 = indirect/claim-like
    note: str = ""
    copy: str = ""                   # table / replica / region the record was read from
    available_at: datetime | None = None  # when the evidence item itself was captured

    def sort_key(self):
        return (self.observed_at, self.weight)


@dataclass
class Identity:
    """Everything known about the data subject at the case cut-off."""

    user_id: str | None
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    audit_ids: list[str] = field(default_factory=list)

    def tokens(self) -> set[str]:
        out = {f"email:{e}" for e in self.emails}
        out |= {f"phone:{p}" for p in self.phones}
        out |= {f"hash:{h}" for h in self.hashes}
        if self.user_id:
            out.add(f"user:{self.user_id}")
        return out


@dataclass
class ResourceAssessment:
    """Final state and required treatment of one relevant resource."""

    resource_id: str
    system_id: str
    category: str
    state: str                  # PRESENT / ABSENT / EXPOSED / REDACTED / ANONYMIZED / ACTIVE / DISABLED / RESTORABLE / BLOCKED / UNKNOWN
    evidence_id: str            # evidence proving the state ('' when unproven)
    required_action: str        # DELETE / REDACT / ANONYMIZE / RETAIN / DISABLE / BLOCK


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""

    def as_dict(self, step_no: int) -> dict:
        return {
            "step_no": step_no,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }


@dataclass
class CaseResult:
    case_id: str
    deletion_status: str
    steps: list[Step]
    notes: list[str] = field(default_factory=list)
