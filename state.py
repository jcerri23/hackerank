"""Step 4 - Reconstruct each relevant resource's state at audit_at.

Rules:
  * only observations whose own timestamp is <= audit_at are used (later evidence must not
    change the answer);
  * reported workflow statuses and operation results are never used as state: only reads
    of the systems (snapshots, look-ups, scans, read-back logs, check activities);
  * the most recent informative observation wins; observations of the same instant from
    the same evidence are merged (any replica copy still holding the data keeps it PRESENT,
    any copy still exposing identifiers keeps PII exposed);
  * a typed full-table snapshot that does not contain a resource of its system proves the
    resource ABSENT at that instant (scoped absence proof).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import config as C
from .evidence import EvidenceIndex
from .models import Observation

CATEGORY_PRIORITY = [
    C.SUPPORT_ATTACHMENT, C.MARKETING_PROFILE, C.SECONDARY_IDENTIFIER, C.LEGAL_HOLD_RECORD,
    C.ACCOUNT_TOMBSTONE, C.EMAIL_DELIVERY_LOG, C.PRODUCT_TELEMETRY, C.CACHE_ENTRY,
]


@dataclass
class ResourceState:
    resource_id: str
    system_id: str
    category: str
    presence: str | None = None      # PRESENT / ABSENT / None (never observed)
    pii: bool | None = None
    enabled: bool | None = None
    restore_allowed: bool | None = None
    attrs: dict = field(default_factory=dict)
    evidence_id: str = ""
    observed_at: datetime | None = None
    history: list[Observation] = field(default_factory=list)


def _informative(o: Observation) -> bool:
    return o.presence is not None or "enabled" in o.attrs or "restore_allowed" in o.attrs


def _category(obs: list[Observation], system: str) -> str:
    cats = [o.category for o in obs if o.category]
    for c in CATEGORY_PRIORITY:
        if c in cats:
            return c
    if cats:
        return max(set(cats), key=cats.count)
    return C.SYSTEM_DEFAULT_CATEGORY.get(system, "UNKNOWN")


class StateReconstructor:
    def __init__(self, index: EvidenceIndex):
        self.index = index

    def state(self, rid: str, audit_at: datetime, since: datetime | None = None) -> ResourceState:
        system = C.system_for_resource(rid) or ""
        all_obs = list(self.index.by_resource.get(rid, []))
        if since is not None:
            # the audit is about the state *after* the request: earlier reads describe the
            # data before deletion started and cannot prove its current state
            all_obs = [o for o in all_obs if o.observed_at >= since]
        direct = [o for o in all_obs if o.observed_at <= audit_at and (o.available_at or o.observed_at) <= audit_at
                  and o.presence is not None]
        if not direct and system == "account_service":
            # scoped absence proof, only when no system read mentions the record at all:
            # the latest complete capture of its system that does not list it
            scopes = [sc for sc in self.index.scopes.get(system, [])
                      if rid not in sc.attrs["present"] and sc.observed_at <= audit_at
                      and (since is None or sc.observed_at >= since)
                      and (sc.available_at or sc.observed_at) <= audit_at]
            if scopes:
                sc = max(scopes, key=lambda x: x.observed_at)
                all_obs.append(Observation(
                    evidence_id=sc.evidence_id, observed_at=sc.observed_at, resource_id=rid,
                    system_id=system, presence="ABSENT", weight=1, note="scope-absence",
                    available_at=sc.available_at))
        # evidence items captured after the cut-off are not usable as proof
        usable = [o for o in all_obs if o.observed_at <= audit_at and (o.available_at or o.observed_at) <= audit_at]
        # a record's data category does not change over time: use every read of it
        st = ResourceState(rid, system, _category(self.index.by_resource.get(rid, []), system), history=usable)
        info = [o for o in usable if _informative(o)]
        fallback = False
        if not info:
            # last resort: a later export that still timestamps the record before the cut-off
            # tells us the record existed, but it cannot be cited as proof.
            info = [o for o in all_obs if o.observed_at <= audit_at and _informative(o)]
            fallback = True
        if not info:
            return st
        latest_t = max(o.observed_at for o in info)
        group = [o for o in info if o.observed_at == latest_t]
        # prefer direct reads of a system over check activities/OCR at the same instant
        best_w = max(o.weight for o in group)
        group = [o for o in group if o.weight == best_w]
        evid = sorted({o.evidence_id for o in group})[0]
        group = [o for o in group if o.evidence_id == evid]
        presences = {o.presence for o in group if o.presence}
        st.presence = "PRESENT" if "PRESENT" in presences else ("ABSENT" if presences else None)
        piis = [o.pii for o in group if o.pii is not None and o.presence == "PRESENT"]
        st.pii = any(piis) if piis else None
        for o in group:
            st.attrs.update(o.attrs)
        st.enabled = _last_attr(info, "enabled", latest_t)
        st.restore_allowed = _last_attr(info, "restore_allowed", latest_t)
        if st.presence is None and (st.enabled is not None or st.restore_allowed is not None):
            st.presence = "PRESENT"
        st.evidence_id = "" if fallback else evid
        st.observed_at = latest_t
        return st


def _last_attr(obs: list[Observation], key: str, at: datetime):
    vals = [(o.observed_at, o.weight, o.attrs[key]) for o in obs if key in o.attrs and o.observed_at <= at]
    if not vals:
        return None
    return sorted(vals, key=lambda v: (v[0], v[1]))[-1][2] in (True, 1, "1", "true", "True")
