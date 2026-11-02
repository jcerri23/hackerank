"""Step 3 - Which resources belong to the data subject?

Three sources of ownership, from strongest to weakest:

1. Identifier links.  Any observation available at audit_at that carries one of the
   subject's tokens (current or historical e-mail, phone, e-mail hash, user id) links its
   resource to the subject.  Matching is exact, so look-alike decoys are never pulled in.
2. Recreation links.  A connector or restore job that repopulated one of the subject's
   resources is itself a recreation path for the subject.
3. Deletion-job links.  Some records keep no identifier at all (an anonymised analytics
   profile, a primary profile that now returns 404).  The only participant-visible trace
   of their owner is the deletion worker's job that touched them.  A job is a profile
   submission followed by downstream actions on a fixed cadence, so the claims of one job
   share a timing signature (their seconds are congruent modulo 30 up to a family offset).
   The offset of each claim family is *learned at runtime* from jobs whose claims are
   identifier-linked to one subject, then used to rebuild every job.  Jobs are assigned
   to requests globally: a job containing a record owned by the subject is theirs; a job
   containing a record owned by someone else is excluded; remaining jobs are resolved by
   elimination across all requests reported in the same window.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from .evidence import EvidenceIndex
from .evidence.activity_parser import Claim
from .models import Identity, Request

PERIOD = 30
TOL = 2


def claim_family(c: Claim) -> str:
    d = c.details
    if "submitted primary profile" in d:
        return "submit"
    if d.startswith("Policy action completed"):
        return "policy"
    if "received the final result" in d or c.event_type.startswith("CHECK_"):
        return "result"
    return "story"


@dataclass
class Job:
    account: Claim
    downstream: list[Claim] = field(default_factory=list)

    def resources(self) -> list[str]:
        return [self.account.resource_id] + [c.resource_id for c in self.downstream]


class OwnershipModel:
    """Global resource -> owners map, learned job cadence and job reconstruction."""

    def __init__(self, index: EvidenceIndex, token_owner: dict[str, set[str]]):
        self.index = index
        self.owners: dict[str, set[str]] = defaultdict(set)
        for rid, obs_list in index.by_resource.items():
            for o in obs_list:
                for t in o.identifiers:
                    self.owners[rid] |= token_owner.get(t, set())
        self.claims = sorted(index.activity.claims, key=lambda c: c.at)
        self.offsets = self._learn_cadence()
        self.jobs = self._build_jobs()
        in_jobs = {id(c) for j in self.jobs for c in [j.account] + j.downstream}
        self.story_claims = [c for c in self.claims if claim_family(c) == "story" and id(c) not in in_jobs]

    def _single_owner(self, rid: str):
        own = self.owners.get(rid, set())
        return next(iter(own)) if len(own) == 1 else None

    def _learn_cadence(self) -> dict[str, dict]:
        """Learn the deletion worker's cadence from identifier-linked claims only:
        (a) per family, the exact gaps between consecutive actions of one subject;
        (b) per family, the seconds component (mod 60) of the gap between the subject's
            profile submission and the head of its action chain."""
        steps: dict[str, Counter] = defaultdict(Counter)
        by_owner: dict[str, list[Claim]] = defaultdict(list)
        for c in self.claims:
            o = self._single_owner(c.resource_id)
            if o:
                by_owner[o].append(c)
        for owner, cl in by_owner.items():
            for fam in ("policy", "result"):
                seq = sorted((c for c in cl if claim_family(c) == fam), key=lambda c: c.at)
                for x, y in zip(seq, seq[1:]):
                    gap = int((y.at - x.at).total_seconds())
                    if 0 < gap < 3600:
                        steps[fam][gap] += 1
        model = {fam: {"steps": sorted(k for k, v in cnt.items() if v >= 2), "first_mod60": []}
                 for fam, cnt in steps.items()}
        self.offsets = model
        self.chains = self._build_chains()
        first: dict[str, Counter] = defaultdict(Counter)
        for chain in self.chains:
            fam = claim_family(chain[0])
            owners = {self._single_owner(c.resource_id) for c in chain} - {None}
            if len(owners) != 1:
                continue
            owner = owners.pop()
            for a in by_owner.get(owner, []):
                if claim_family(a) == "submit" and timedelta(0) < chain[0].at - a.at < timedelta(hours=4):
                    first[fam][int((chain[0].at - a.at).total_seconds()) % 60] += 1
        for fam, cnt in first.items():
            peak = cnt.most_common(1)[0][1]
            model[fam]["first_mod60"] = sorted(k for k, v in cnt.items() if v >= max(2, peak * 0.3))
        return model

    def _build_chains(self) -> list[list[Claim]]:
        """Link claims of one family whose gap equals a learned step (+-2 s) into chains."""
        chains: list[list[Claim]] = []
        nxt_of: dict[int, Claim] = {}
        has_prev: set[int] = set()
        for fam in ("policy", "result"):
            steps = self.offsets.get(fam, {}).get("steps", [])
            cl = sorted((c for c in self.claims if claim_family(c) == fam and c.system_id != "account_service"),
                        key=lambda c: c.at)
            for i, x in enumerate(cl):
                best = None
                for y in cl[i + 1:]:
                    gap = (y.at - x.at).total_seconds()
                    if gap > 3600:
                        break
                    if id(y) in has_prev:
                        continue
                    err = min((abs(gap - s) for s in steps), default=99)
                    if err <= 2 and (best is None or err < best[0]):
                        best = (err, y)
                if best:
                    nxt_of[id(x)] = best[1]
                    has_prev.add(id(best[1]))
            for c in cl:
                if id(c) in has_prev:
                    continue
                chain = [c]
                while id(chain[-1]) in nxt_of:
                    chain.append(nxt_of[id(chain[-1])])
                chains.append(chain)
        return chains

    def _build_jobs(self) -> list[Job]:
        """Attach each action chain to the profile submission whose gap to the chain head
        has the learned seconds component (global best-first matching)."""
        accounts = [c for c in self.claims if c.system_id == "account_service" and claim_family(c) in ("submit", "story")]
        jobs = {id(a): Job(a) for a in accounts}
        taken: set[int] = set()

        def circ(a, b):
            d = abs(a - b) % 60
            return min(d, 60 - d)

        chain_owners = [{self._single_owner(c.resource_id) for c in ch} - {None} for ch in self.chains]
        pairs = []
        for a in accounts:
            if claim_family(a) != "submit":
                continue
            a_owner = self._single_owner(a.resource_id)
            for ci, chain in enumerate(self.chains):
                head = chain[0]
                fam = claim_family(head)
                offs = self.offsets.get(fam, {}).get("first_mod60", [])
                gap = (head.at - a.at).total_seconds()
                if not offs or not (0 < gap <= 4 * 3600):
                    continue
                owners = chain_owners[ci]
                if a_owner and owners and a_owner not in owners:
                    continue          # identifiers prove the chain belongs to someone else
                prio = 0 if (a_owner and a_owner in owners) else 1
                d = min(circ(int(gap) % 60, o) for o in offs)
                if d <= 1:
                    pairs.append((prio, d, gap, id(a), ci))
        pairs.sort()
        started: set[int] = set()
        used_chain: set[int] = set()
        for prio, d, gap, ia, ci in pairs:
            if ia in started or ci in used_chain:
                continue
            jobs[ia].downstream.extend(self.chains[ci])
            taken.update(id(c) for c in self.chains[ci])
            started.add(ia)
            used_chain.add(ci)
        # story jobs (hand-written workflow messages) keep their claims within 30 minutes
        for a in accounts:
            if claim_family(a) != "story":
                continue
            for c in self.claims:
                if id(c) in taken or c.system_id == "account_service" or claim_family(c) != "story":
                    continue
                if timedelta(0) < c.at - a.at <= timedelta(minutes=30):
                    jobs[id(a)].downstream.append(c)
                    taken.add(id(c))
        return list(jobs.values())


class JobAssigner:
    """Assign deletion jobs to requests (global, by evidence and elimination)."""

    def __init__(self, model: OwnershipModel, requests: list[tuple[Request, str | None]]):
        self.model = model
        self.assignment: dict[str, Job] = {}
        self._assign(requests)

    def _window(self, req: Request):
        return req.reported_at - timedelta(hours=6), req.reported_at + timedelta(hours=1)

    def _assign(self, requests):
        possible: dict[str, list[Job]] = {}
        definite: dict[str, Job] = {}
        for req, uid in requests:
            if not uid:
                continue
            lo, hi = self._window(req)
            cands = []
            for job in self.model.jobs:
                if not (lo <= job.account.at <= hi) or job.account.at > req.audit_at:
                    continue
                owners = set().union(*(self.model.owners.get(r, set()) for r in job.resources()))
                if uid in owners and len(owners) == 1:
                    definite.setdefault(req.case_id, job)
                elif not owners:
                    cands.append(job)
            if req.case_id not in definite:
                possible[req.case_id] = cands
        taken = {id(j) for j in definite.values()}
        changed = True
        while changed:
            changed = False
            for cid, cands in possible.items():
                if cid in definite:
                    continue
                left = [j for j in cands if id(j) not in taken]
                if len(left) == 1:
                    definite[cid] = left[0]
                    taken.add(id(left[0]))
                    changed = True
        # Tie-break (only when identifiers, cadence and elimination cannot decide): the
        # worker processes its queue in intake order, so the k-th undecided request of a
        # window takes the k-th undecided job by the time of its first downstream action.
        self.tie_broken = []
        order = lambda cid: int("".join(ch for ch in cid if ch.isdigit()) or 0)
        job_key = lambda j: (j.downstream[0].at if j.downstream else j.account.at)
        for cid in sorted(possible, key=order):
            if cid in definite:
                continue
            left = sorted((j for j in possible[cid] if id(j) not in taken), key=job_key)
            if left:
                definite[cid] = left[0]
                taken.add(id(left[0]))
                self.tie_broken.append(cid)
        self.assignment = definite


class Linker:
    def __init__(self, index: EvidenceIndex, ownership: OwnershipModel, assigner: JobAssigner):
        self.index = index
        self.own = ownership
        self.assigner = assigner

    def link(self, req: Request, ident: Identity) -> dict[str, str]:
        """Return {resource_id: how_linked} for every resource relevant to the subject."""
        tokens = ident.tokens()
        cutoff = req.audit_at
        linked: dict[str, str] = {}
        for t in tokens:
            for rid in self.index.by_token.get(t, ()):
                if any(t in o.identifiers for o in self.index.by_resource[rid] if o.observed_at <= cutoff):
                    linked.setdefault(rid, "identifier")
        for src, dst, at, _aid in self.index.activity.recreation_edges:
            if at <= cutoff and dst in linked and src not in linked:
                linked[src] = "recreation"
        job = self.assigner.assignment.get(req.case_id)
        uid = ident.user_id
        if job:
            for c in [job.account] + job.downstream:
                owners = self.own.owners.get(c.resource_id, set())
                if c.at <= cutoff and (not owners or owners == {uid}):
                    linked.setdefault(c.resource_id, "deletion-job")
            # older profiles of the same person (closed by an earlier request) are not part
            # of this request unless they still hold data
            self.job_account = job.account.resource_id
        else:
            self.job_account = None
            # hand-written workflow messages: claims reported right before the request was
            # reported as done, on records nobody else owns
            lo, hi = req.reported_at - timedelta(minutes=20), req.reported_at + timedelta(minutes=15)
            for c in self.own.story_claims:
                if lo <= c.at <= hi and c.at <= cutoff and not self.own.owners.get(c.resource_id):
                    linked.setdefault(c.resource_id, "deletion-job(story)")
        return linked
