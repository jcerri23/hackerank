"""Step 6 - Turn policy decisions into the ordered steps_json plan.

COMPLETE   -> only verification steps, each citing the evidence that proves the state.
INCOMPLETE -> findings (cited) + full remediation plan + post-remediation checks (uncited).
Ordering: phase (FINDING, REMEDIATION, VERIFICATION), then the canonical operation order
(which already puts DISABLE_DATA_FLOW and BLOCK_RESTORE before any record change, so
nothing can be recreated while it is being removed), then system_id, then resource_id.
"""

from __future__ import annotations

from . import config as C
from .models import Step
from .policy import Decision


def _sort(steps: list[Step]) -> list[Step]:
    return sorted(steps, key=lambda s: (C.PHASE_ORDER[s.phase], C.OP_ORDER[s.operation], s.system_id, s.resource_id))


def plan(decisions: list[Decision]) -> tuple[str, list[Step]]:
    decisions = [d for d in decisions if d is not None]
    incomplete = any(d.violated for d in decisions)
    steps: list[Step] = []
    seen = set()
    for d in decisions:
        if incomplete:
            if d.finding:
                steps.append(Step(C.FINDING, d.system_id, d.resource_id, d.finding, C.OP_STATE[d.finding], d.evidence_id))
            if d.remediation:
                steps.append(Step(C.REMEDIATION, d.system_id, d.resource_id, d.remediation, C.OP_STATE[d.remediation], ""))
            if d.verification:
                steps.append(Step(C.VERIFICATION, d.system_id, d.resource_id, d.verification, C.OP_STATE[d.verification], ""))
        elif d.verification:
            steps.append(Step(C.VERIFICATION, d.system_id, d.resource_id, d.verification, C.OP_STATE[d.verification], d.evidence_id))
    unique = []
    for s in _sort(steps):
        key = (s.phase, s.operation, s.system_id, s.resource_id)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return (C.INCOMPLETE if incomplete else C.COMPLETE), unique
