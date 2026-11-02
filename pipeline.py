"""Orchestrates the six steps for every request.

    intent -> identity -> linking -> state -> policy -> planner

The evidence index, identity resolver and ownership model are built once and shared.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from . import config as C
from .evidence import EvidenceIndex, build_index
from .identity import IdentityResolver
from .intent import NEURAL, classify
from .linking import JobAssigner, Linker, OwnershipModel
from .loaders import Dataset, load_dataset, load_requests
from .models import CaseResult, Request
from .normalize import email_hash, norm_name, norm_phone
from .planner import plan
from .policy import PolicyEngine
from .state import StateReconstructor


@dataclass
class Auditor:
    ds: Dataset = field(default_factory=load_dataset)
    verbose: bool = True

    def __post_init__(self):
        self.resolver = IdentityResolver(self.ds.identities, self.ds.audit_log)
        self.index: EvidenceIndex = build_index(self.ds, self.resolver.all_emails(), verbose=self.verbose)
        self.ownership = OwnershipModel(self.index, self._token_owner())
        # deletion jobs are assigned once, over every participant-visible request
        all_requests = load_requests("deletion_requests.csv") + load_requests("sample_deletion_requests.csv")
        subjects = []
        for r in all_requests:
            if classify(r.request_text).is_deletion:
                subjects.append((r, self.resolver.resolve(r.email, r.audit_at).user_id))
        self.assigner = JobAssigner(self.ownership, subjects)
        self.linker = Linker(self.index, self.ownership, self.assigner)
        self.states = StateReconstructor(self.index)
        self.policy = PolicyEngine(self.ds.rules)
        if self.verbose:
            print(f"[linking] learned deletion-job cadence offsets: {self.ownership.offsets}; "
                  f"{len(self.ownership.jobs)} jobs, {len(self.assigner.assignment)} assigned", file=sys.stderr)

    def _token_owner(self) -> dict[str, set[str]]:
        owner: dict[str, set[str]] = {}

        def add(tok, uid):
            owner.setdefault(tok, set()).add(uid)

        for r in self.ds.identities:
            uid = r["user_id"]
            add(f"user:{uid.lower()}", uid)
            add(f"email:{r['email'].lower()}", uid)
            add(f"hash:{r['email_hash'].lower()}", uid)
            add(f"hash:{email_hash(r['email'])}", uid)
            add(f"phone:{norm_phone(r['phone'])}", uid)
        for r in self.ds.audit_log:
            uid, f = r["user_id"], r["field_name"]
            for v in (r["old_value"], r["new_value"]):
                if f == "email":
                    add(f"email:{v.lower()}", uid)
                    add(f"hash:{email_hash(v)}", uid)
                elif f == "email_hash":
                    add(f"hash:{v.lower()}", uid)
                elif f == "phone":
                    add(f"phone:{norm_phone(v)}", uid)
        return owner

    def audit(self, req: Request, debug: bool = False) -> CaseResult:
        intent = classify(req.request_text)
        notes = [f"intent={intent.method}:{intent.is_deletion}"]
        if not intent.is_deletion:
            return CaseResult(req.case_id, C.NOT_APPLICABLE, [], notes)
        ident = self.resolver.resolve(req.email, req.audit_at)
        linked = self.linker.link(req, ident)
        job_account = self.linker.job_account
        decisions = []
        for rid, how in sorted(linked.items()):
            st = self.states.state(rid, req.audit_at, since=req.requested_at)
            if st.presence is None and st.category != C.USER_PROFILE:
                continue  # never observed: nothing to verify or remediate
            if (st.category == C.USER_PROFILE and job_account and rid != job_account
                    and st.presence != "PRESENT"):
                continue  # an older, already-closed profile belongs to an earlier request
            d = self.policy.decide(st, req.audit_at)
            if debug:
                print(f"  {rid:20} {how:22} cat={st.category:20} pres={st.presence} pii={st.pii} "
                      f"en={st.enabled} ra={st.restore_allowed} ev={st.evidence_id} -> "
                      f"{d.finding if d else None}/{d.verification if d else None}", file=sys.stderr)
            if d:
                decisions.append(d)
        status, steps = plan(decisions)
        return CaseResult(req.case_id, status, steps, notes)

    def neural_calls(self) -> int:
        return NEURAL.calls
