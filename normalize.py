"""Normalisation helpers: timestamps and identifier tokens."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b", re.IGNORECASE)
PHONE_RE = re.compile(r"\+\d[\d\s().\-]{6,}\d")
RESOURCE_RE = re.compile(
    r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|"
    r"legal_hold|cache|email_delivery|marketing|telemetry|object)_\d+\b"
)

NULLS = {None, "", "null", "none", "None", "NULL", "n/a", "-"}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm_email(v: str) -> str:
    return v.strip().strip('"').lower()


def norm_phone(v: str) -> str:
    return re.sub(r"\D", "", v)


def email_hash(email: str) -> str:
    return hashlib.sha256(norm_email(email).encode()).hexdigest()


def norm_name(v: str) -> str:
    v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", v).strip().lower()


def is_null(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() in NULLS)


def tokens_from_value(value) -> set[str]:
    """Extract identifier tokens from any scalar value found in evidence."""
    out: set[str] = set()
    if is_null(value) or isinstance(value, bool):
        return out
    s = str(value)
    for m in EMAIL_RE.findall(s):
        out.add(f"email:{norm_email(m)}")
    for m in HASH_RE.findall(s):
        out.add(f"hash:{m.lower()}")
    for m in USER_RE.findall(s):
        out.add(f"user:{m.lower()}")
    for m in PHONE_RE.findall(s):
        digits = norm_phone(m)
        if len(digits) >= 8:
            out.add(f"phone:{digits}")
    return out


def tokens_from_fields(record: dict, keys=None) -> set[str]:
    out: set[str] = set()
    for k, v in record.items():
        if keys is not None and k not in keys:
            continue
        if isinstance(v, dict):
            out |= tokens_from_fields(v)
        elif isinstance(v, list):
            for item in v:
                out |= tokens_from_fields(item) if isinstance(item, dict) else tokens_from_value(item)
        else:
            out |= tokens_from_value(v)
    return out


def direct_pii(tokens: set[str]) -> bool:
    """Direct identifiers = email, phone or email hash (a user_id reference is allowed)."""
    return any(t.startswith(("email:", "phone:", "hash:")) for t in tokens)
