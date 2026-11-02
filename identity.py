"""Step 2 - Resolve the data subject and every identifier they had up to audit_at.

The request e-mail may be the current one or an older one.  We locate the user through
the current snapshot and the audit log, then rebuild the identity as it was at audit_at:
changes made *after* the cut-off are rolled back (that value did not exist yet), while every
value replaced *before* the cut-off is kept as a historical identifier, because old copies
of the data are exactly where ghosts hide.  E-mail hashes are recomputed (sha256 of the
lower-cased e-mail) so systems that only store hashes can be matched too.
Matching is exact after normalisation: look-alike decoy addresses are never merged.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .models import Identity
from .normalize import email_hash, norm_email, norm_name, norm_phone, parse_ts


class IdentityResolver:
    def __init__(self, identities: list[dict], audit_log: list[dict]):
        self.current = {r["user_id"]: r for r in identities}
        self.events = defaultdict(list)
        self.email_owner: dict[str, set[str]] = defaultdict(set)
        for r in identities:
            self.email_owner[norm_email(r["email"])].add(r["user_id"])
        for r in audit_log:
            r = dict(r)
            r["_at"] = parse_ts(r["event_time"])
            self.events[r["user_id"]].append(r)
            if r["field_name"] == "email":
                self.email_owner[norm_email(r["old_value"])].add(r["user_id"])
                self.email_owner[norm_email(r["new_value"])].add(r["user_id"])
        for evs in self.events.values():
            evs.sort(key=lambda e: e["_at"])

    def all_emails(self) -> set[str]:
        return set(self.email_owner)

    def resolve(self, email: str, audit_at: datetime) -> Identity:
        e = norm_email(email)
        owners = sorted(self.email_owner.get(e, set()))
        if not owners:
            ident = Identity(user_id=None, emails={e}, hashes={email_hash(e)})
            return ident
        uid = owners[0]
        cur = self.current.get(uid, {})
        state = {
            "email": norm_email(cur.get("email", "")) if cur else e,
            "phone": cur.get("phone", ""),
            "email_hash": (cur.get("email_hash") or "").lower(),
            "name": cur.get("name", ""),
        }
        history: dict[str, set[str]] = defaultdict(set)
        audit_ids = []
        # roll back changes that happened after the cut-off
        for ev in reversed(self.events.get(uid, [])):
            if ev["_at"] > audit_at:
                state[ev["field_name"]] = ev["old_value"]
        for ev in self.events.get(uid, []):
            if ev["_at"] <= audit_at:
                history[ev["field_name"]].add(ev["old_value"])
                history[ev["field_name"]].add(ev["new_value"])
                audit_ids.append(ev["audit_id"])
        emails = {norm_email(state["email"])} | {norm_email(x) for x in history["email"]} | {e}
        phones = {norm_phone(state["phone"])} | {norm_phone(x) for x in history["phone"]}
        hashes = {state["email_hash"].lower()} | {x.lower() for x in history["email_hash"]}
        hashes |= {email_hash(x) for x in emails}
        return Identity(
            user_id=uid,
            emails={x for x in emails if x},
            phones={x for x in phones if len(x) >= 8},
            hashes={x for x in hashes if x},
            names={norm_name(state["name"])} if state.get("name") else set(),
            audit_ids=audit_ids,
        )
