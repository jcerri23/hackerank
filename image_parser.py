"""Screenshots -> Observations with neural OCR (Tesseract 5 LSTM engine).

Pipeline per image:
  1. grayscale + 2x Lanczos upscale (small UI fonts are the main error source);
  2. Tesseract LSTM in sparse-text mode (--psm 11) returning word boxes;
  3. words regrouped into visual rows by their vertical centre;
  4. each row that shows a resource id yields an Observation carrying the identifiers
     printed on the same row; OCR-mangled e-mails are snapped to the closest known
     identity e-mail (edit distance <= 2) so `areedt433@` still matches `areed1433@`.
Results are cached under .cache/ocr so repeated runs are deterministic and fast.
If Tesseract / pytesseract is unavailable the parser returns nothing (logged by caller).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .. import config as C
from ..models import Observation
from ..normalize import EMAIL_RE, RESOURCE_RE, tokens_from_value

KNOWN_EMAILS: set[str] = set()
FILE_RE = re.compile(r"\b[\w\-]+\.(?:pdf|zip|jpg|png)\b")
OCR_ENGINE = "tesseract-lstm(psm=11, 2x lanczos)"
STATS = {"images": 0, "rows": 0, "cache_hits": 0}


def _levenshtein(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def snap_email(raw: str) -> str:
    e = raw.lower()
    if e in KNOWN_EMAILS or not KNOWN_EMAILS:
        return e
    best, best_d = None, 3
    for k in KNOWN_EMAILS:
        if k.split("@")[-1] != e.split("@")[-1] and _levenshtein(k.split("@")[-1], e.split("@")[-1]) > 1:
            continue
        d = _levenshtein(e, k)
        if d < best_d:
            best, best_d = k, d
        elif d == best_d:
            best = None  # ambiguous: refuse to guess
    return best or e


def _ocr_rows(path: Path) -> list[str]:
    cache = C.CACHE_DIR / "ocr" / (path.stem + ".json")
    if cache.exists():
        STATS["cache_hits"] += 1
        return json.loads(cache.read_text())
    import pytesseract
    from PIL import Image

    im = Image.open(path).convert("L")
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    d = pytesseract.image_to_data(im, config="--psm 11", output_type=pytesseract.Output.DICT)
    words = sorted(
        (d["top"][i] + d["height"][i] // 2, d["left"][i], d["text"][i].strip())
        for i in range(len(d["text"]))
        if d["text"][i].strip() and float(d["conf"][i]) > 20
    )
    rows: list[list] = []
    for y, x, t in words:
        if rows and abs(rows[-1][0] - y) <= 12:
            rows[-1][1].append((x, t))
        else:
            rows.append([y, [(x, t)]])
    lines = [" ".join(t for _, t in sorted(ws)) for _, ws in rows]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(lines, ensure_ascii=False, indent=0))
    return lines


def parse_image(path: Path, evidence_id: str, captured_at: datetime) -> list[Observation]:
    try:
        lines = _ocr_rows(path)
    except Exception:  # tesseract missing or unreadable image
        return []
    STATS["images"] += 1
    STATS["rows"] += len(lines)
    out: list[Observation] = []
    # detail screens show one record per system; everything printed on such a screen
    # (contact card, header, side panel) belongs to that record
    all_rids = RESOURCE_RE.findall(" ".join(lines))
    per_system = {}
    for rid in set(all_rids):
        per_system.setdefault(C.system_for_resource(rid), set()).add(rid)
    page_tokens = tokens_from_value(EMAIL_RE.sub(lambda m: snap_email(m.group(0)), " ".join(lines)))
    for line in lines:
        rids = RESOURCE_RE.findall(line)
        files = FILE_RE.findall(line)
        if not rids and not files:
            continue
        fixed = EMAIL_RE.sub(lambda m: snap_email(m.group(0)), line)
        tokens = tokens_from_value(fixed)
        pii = any(t.startswith(("email:", "phone:")) for t in tokens)
        low = line.lower()
        for rid in rids:
            system = C.system_for_resource(rid)
            if not system:
                continue
            row_tokens = tokens
            if len(per_system.get(system, ())) == 1 and len({t for t in page_tokens if t.startswith("email:")}) == 1:
                row_tokens = tokens | page_tokens
            attrs = {}
            if system == "integration_service" and ("continuous" in low or "healthy" in low):
                attrs["enabled"] = True       # connector shown running in the console
            out.append(Observation(
                evidence_id=evidence_id, observed_at=captured_at, resource_id=rid, system_id=system,
                presence="PRESENT",
                pii=any(t.startswith(("email:", "phone:")) for t in row_tokens) if row_tokens else None,
                identifiers=frozenset(row_tokens),
                category=C.SYSTEM_DEFAULT_CATEGORY.get(system), attrs=attrs, weight=1, note="image:ocr-row"))
        if not rids:
            # object listings show file names only; resolved later through catalog aliases
            for f in files:
                out.append(Observation(
                    evidence_id=evidence_id, observed_at=captured_at, resource_id=f"alias:{f}",
                    system_id="", presence="PRESENT", pii=None, identifiers=frozenset(tokens),
                    weight=1, note="image:ocr-file"))
    return out
