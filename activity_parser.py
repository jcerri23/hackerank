"""system_activity.csv -> Observations, links and deletion-worker claims.

Activity rows are *claims* about operations, not proof of state.  Only rows whose text
reports a read-back check (CHECK_DATA_FLOW / CHECK_RESTORE_ACCESS), an explicit identity
link from the data catalog, or a recreation event (a connector or restore job that
"repopulated" a downstream resource) are turned into observations.  Deletion-worker
operations are kept separately as claims: they tell us which resources the workflow
touched for a request, which is how records without any identifier left are attributed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .. import config as C
from ..models import Observation
from ..normalize import RESOURCE_RE, parse_ts, tokens_from_value

LINK_RE = re.compile(r"linked (\S+) \(([^)]*)\) to (\S+) and (user_\d+)", re.IGNORECASE)
ATTACH_RE = re.compile(r"confirmed (\S+) \(([^)]*)\) belongs only to (\S+) and (user_\d+)", re.IGNORECASE)
REPOP_RE = re.compile(r"repopulated downstream resource (\S+?)\.?$")


@dataclass
class Claim:
    activity_id: str
    at: datetime
    event_type: str
    resource_id: str
    system_id: str
    result: str
    details: str


@dataclass
class ActivityIndex:
    observations: list[Observation]
    claims: list[Claim]                  # deletion_worker operations
    aliases: dict[str, str]              # business reference -> resource id
    exclusive_attachments: set[str]      # resources/files that belong to a single subject
    non_deletion_accounts: dict[str, datetime]  # account resource -> classification time
    recreation_edges: list[tuple[str, str, datetime, str]]  # (source, target, at, activity_id)


def parse_activity(rows: list[dict]) -> ActivityIndex:
    obs: list[Observation] = []
    claims: list[Claim] = []
    aliases: dict[str, str] = {}
    exclusive: set[str] = set()
    non_deletion: dict[str, datetime] = {}
    edges = []
    for r in rows:
        at = parse_ts(r["event_time"])
        et, rid, det = r["event_type"], r["resource_id"], r["details"]
        system = C.system_for_resource(rid) or r["target_system"]
        aid = r["activity_id"]
        if et == "LINK_RESOURCE_IDENTITY":
            m = LINK_RE.search(det) or ATTACH_RE.search(det)
            if m:
                target, alias, email, user = m.groups()
                if alias and alias != target:
                    aliases[alias] = target
                if ATTACH_RE.search(det):
                    exclusive.add(target)
                    if alias:
                        exclusive.add(alias)
                obs.append(Observation(
                    evidence_id=aid, observed_at=at, resource_id=target, system_id=system,
                    presence=None, identifiers=frozenset(tokens_from_value(email) | tokens_from_value(user)),
                    kind="activity", weight=1, note="activity:catalog-link"))
            continue
        if et == "CHECK_DATA_FLOW" and r["reported_result"] == "SUCCESS":
            enabled = "remains enabled" in det
            obs.append(Observation(evidence_id=aid, observed_at=at, resource_id=rid, system_id=system,
                                   presence="PRESENT", attrs={"enabled": enabled}, category=C.DATA_FLOW,
                                   kind="activity", weight=1, note="activity:check-flow"))
        elif et == "CHECK_RESTORE_ACCESS" and r["reported_result"] == "SUCCESS":
            allowed = "can still be restored" in det
            obs.append(Observation(evidence_id=aid, observed_at=at, resource_id=rid, system_id=system,
                                   presence="PRESENT", attrs={"restore_allowed": allowed},
                                   category=C.BACKUP_ARCHIVE, kind="activity", weight=1,
                                   note="activity:check-restore"))
        elif et in ("SYNC_RECORD", "RESTORE_BACKUP"):
            m = REPOP_RE.search(det)
            if m:
                edges.append((rid, m.group(1), at, aid))
        elif et == "CLASSIFY_REQUEST":
            non_deletion[rid] = at
        if r["source_system"] == "deletion_worker":
            claims.append(Claim(aid, at, et, rid, system, r["reported_result"], det))
    return ActivityIndex(obs, claims, aliases, exclusive, non_deletion, edges)
