"""Custom evaluation cases (beyond the 32 public samples).

Each case targets one failure mode named in the problem statement and checks the
pipeline's behaviour directly on the real evidence index.

    python3 code/evaluation/custom_cases.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost_audit.intent import classify  # noqa: E402
from ghost_audit.loaders import load_requests  # noqa: E402
from ghost_audit.pipeline import Auditor  # noqa: E402
from ghost_audit.policy import PolicyEngine  # noqa: E402
from ghost_audit.state import ResourceState  # noqa: E402


def run() -> list[tuple[str, bool, str]]:
    auditor = Auditor(verbose=False)
    samples = {r.case_id: r for r in load_requests("sample_deletion_requests.csv")}
    main = {r.case_id: r for r in load_requests("deletion_requests.csv")}
    results = []

    # C1 - look-alike decoy: smartinez6461@ must not pull in smartinez6461.jr@'s search entry
    req = samples["case_28"]
    ident = auditor.resolver.resolve(req.email, req.audit_at)
    linked = auditor.linker.link(req, ident)
    ok = "search_8168" not in linked
    results.append(("C1 decoy e-mail is not linked", ok, f"linked={sorted(linked)}"))

    # C2 - historical identifier: a request sent from an old address resolves the same user
    req = main["case_37"]                       # abena.o189491@zoho.com is a replaced e-mail
    ident = auditor.resolver.resolve(req.email, req.audit_at)
    ok = ident.user_id == "user_35" and len(ident.emails) >= 2
    results.append(("C2 old e-mail resolves to current subject", ok, f"user={ident.user_id} emails={sorted(ident.emails)}"))

    # C3 - time travel: moving audit_at before every post-request read must remove all
    # citations of evidence captured later (here evidence_92, captured 2026-09-15 11:44)
    req = replace(samples["case_26"], audit_at=samples["case_26"].reported_at + timedelta(hours=1))
    res = auditor.audit(req)
    cited = {s.evidence_id for s in res.steps if s.evidence_id}
    ok = "evidence_92" not in cited
    results.append(("C3 evidence after audit_at is never cited", ok, f"status={res.deletion_status} cited={sorted(cited)}"))

    # C4 - multilingual non-deletion intents are NOT_APPLICABLE, deletions are not
    probes = {
        "Bitte ändern Sie nur meine Telefonnummer.": None,       # not covered by rules -> default/neural
        "Please remove me from marketing lists only. Keep my account.": False,
        "Je demande la suppression définitive du compte associé à cette adresse e-mail.": True,
        "このメールアドレスに関連するアカウントの完全な削除を依頼します。": True,
        "Can you tell me what personal information you hold about me?": False,
    }
    bad = [t for t, want in probes.items() if want is not None and classify(t).is_deletion != want]
    results.append(("C4 multilingual intent", not bad, f"wrong={bad}"))

    # C5 - tombstone: keeps the stable user reference, only direct identifiers are redacted
    pe = PolicyEngine(auditor.ds.rules)
    st = ResourceState("tombstone_x", "identity_service", "ACCOUNT_TOMBSTONE", presence="PRESENT", pii=True)
    d = pe.decide(st, req.audit_at)
    st2 = replace(st, pii=False)
    d2 = pe.decide(st2, req.audit_at)
    ok = d.remediation == "REDACT_RECORD" and d2.verification == "VERIFY_RECORD_REDACTED" and not d2.violated
    results.append(("C5 tombstone is redacted, never deleted", ok, f"{d.remediation}/{d2.verification}"))

    # C6 - retained records: an in-retention security record is verified PRESENT, not deleted
    st = ResourceState("audit_record_x", "security_service", "SECURITY_AUDIT_LOG", presence="PRESENT",
                       attrs={"retention_until": "2031-12-31T23:59:59Z"})
    d = pe.decide(st, req.audit_at)
    results.append(("C6 security audit record retained", d.verification == "VERIFY_RECORD_PRESENT" and not d.violated,
                    f"{d.verification}"))
    return results


if __name__ == "__main__":
    rows = run()
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    print(f"{sum(ok for _, ok, _ in rows)}/{len(rows)} passed")
