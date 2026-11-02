"""PDF business documents -> Observations.

PDFs are invoices, support attachments, registers and journal extracts: they never state a
deletion verdict, so we extract facts.  Every resource id printed in the document is
observed as PRESENT and inherits the identifiers printed with it.  Business references that
are not resource ids (order numbers, statement numbers, ticket numbers) are emitted as
`alias:<reference>` observations and resolved to resource ids later (see evidence/__init__).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import config as C
from ..models import Observation
from ..normalize import RESOURCE_RE, parse_ts, tokens_from_value

ALIAS_RE = re.compile(r"\b(?:NST-\d{4}-\d{4}-\d+|ORD-\d{6,}|CS-\d{3,})\b")
RETAIN_RE = re.compile(r"retain(?:ed)?[ -]until\s+(\d{1,2} \w{3} \d{4})", re.IGNORECASE)


def _text(path: Path) -> str:
    try:
        import pypdf
    except ImportError:  # pragma: no cover
        return ""
    reader = pypdf.PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _date(txt: str):
    try:
        return datetime.strptime(txt, "%d %b %Y").replace(hour=23, minute=59, second=59).isoformat() + "Z"
    except ValueError:
        return None


def parse_pdf(path: Path, evidence_id: str, captured_at: datetime) -> list[Observation]:
    text = _text(path)
    if not text:
        return []
    out: list[Observation] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # table-like rows: one observation per row that names a subject (registers)
    row_lines = [l for l in lines if l.count("|") >= 3]
    if row_lines:
        for l in row_lines:
            tokens = tokens_from_value(l)
            retain = RETAIN_RE.search(l)
            for ref in ALIAS_RE.findall(l):
                out.append(Observation(
                    evidence_id=evidence_id, observed_at=captured_at, resource_id=f"alias:{ref}",
                    system_id="", presence="PRESENT", pii=False, identifiers=frozenset(tokens),
                    attrs={"retention_until": _date(retain.group(1))} if retain else {},
                    note="pdf:row"))
        return out
    # free-form document: everything printed belongs to the same subject
    tokens = tokens_from_value(text)
    pii = any(t.startswith(("email:", "phone:")) for t in tokens)
    attrs = {}
    retain = RETAIN_RE.search(text.replace("\n", " "))
    if retain:
        attrs["retention_until"] = _date(retain.group(1))
    rids = set(RESOURCE_RE.findall(text))
    for rid in sorted(rids):
        system = C.system_for_resource(rid)
        category = C.SUPPORT_ATTACHMENT if system == "support_platform" and "attachment" in path.name.lower() + text.lower() else None
        out.append(Observation(
            evidence_id=evidence_id, observed_at=captured_at, resource_id=rid,
            system_id=system or "", presence="PRESENT", pii=pii, identifiers=frozenset(tokens),
            category=category or C.SYSTEM_DEFAULT_CATEGORY.get(system or ""), attrs=dict(attrs),
            note="pdf:document"))
    if not rids:
        for ref in sorted(set(ALIAS_RE.findall(text))):
            out.append(Observation(
                evidence_id=evidence_id, observed_at=captured_at, resource_id=f"alias:{ref}",
                system_id="", presence="PRESENT", pii=pii, identifiers=frozenset(tokens),
                attrs=dict(attrs), note="pdf:document"))
    return out
