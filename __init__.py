"""Evidence index: parse every file in the manifest once and index the observations.

The index is built once per run and shared by all 518 cases.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field

from .. import config as C
from ..loaders import Dataset
from ..models import Observation
from ..normalize import parse_ts
from . import image_parser
from .activity_parser import ActivityIndex, parse_activity
from .document_parser import parse_pdf
from .json_parser import parse_json
from .log_parser import parse_log
from .sql_parser import parse_sql
from .text_parser import parse_text

PARSERS = {
    ".sql": parse_sql,
    ".json": parse_json,
    ".log": parse_log,
    ".txt": parse_text,
    ".pdf": parse_pdf,
    ".png": image_parser.parse_image,   # neural OCR (Tesseract LSTM)
    ".jpg": image_parser.parse_image,
}


@dataclass
class EvidenceIndex:
    observations: list[Observation]
    activity: ActivityIndex
    file_captured_at: dict
    by_resource: dict = field(default_factory=lambda: defaultdict(list))
    by_token: dict = field(default_factory=lambda: defaultdict(set))
    scopes: dict = field(default_factory=lambda: defaultdict(list))   # system -> complete captures
    stats: dict = field(default_factory=dict)

    def build(self):
        self.by_resource.clear()
        self.by_token.clear()
        for o in self.observations:
            self.by_resource[o.resource_id].append(o)
            for t in o.identifiers:
                self.by_token[t].add(o.resource_id)
        for lst in self.by_resource.values():
            lst.sort(key=lambda o: (o.observed_at, o.weight))
        return self


def _resolve_aliases(observations: list[Observation], aliases: dict[str, str]) -> list[Observation]:
    alias_map = dict(aliases)
    for o in observations:
        for key in ("order_reference", "ticket_id", "file_name"):
            v = o.attrs.get(key)
            if v and not o.resource_id.startswith("alias:"):
                alias_map.setdefault(str(v), o.resource_id)
    out = []
    for o in observations:
        if o.resource_id.startswith("alias:"):
            ref = o.resource_id.split(":", 1)[1]
            target = alias_map.get(ref)
            if not target:
                continue
            o.resource_id = target
            o.system_id = C.system_for_resource(target) or ""
            o.category = o.category or C.SYSTEM_DEFAULT_CATEGORY.get(o.system_id)
        out.append(o)
    return out


def build_index(ds: Dataset, known_emails: set[str], verbose: bool = True) -> EvidenceIndex:
    image_parser.KNOWN_EMAILS = known_emails
    observations: list[Observation] = []
    captured = {}
    per_type = defaultdict(int)
    for row in ds.manifest:
        path = C.DATASET_DIR / row["file_path"]
        captured_at = parse_ts(row["captured_at"])
        captured[row["file_id"]] = captured_at
        parser = PARSERS.get(path.suffix.lower())
        if not parser or not path.exists():
            continue
        try:
            found = parser(path, row["file_id"], captured_at)
        except Exception as exc:  # keep going: one bad file must not stop 518 cases
            print(f"[evidence] failed to parse {path.name}: {exc}", file=sys.stderr)
            continue
        for o in found:
            o.available_at = captured_at
        per_type[path.suffix.lower()] += len(found)
        observations.extend(found)
    act = parse_activity(ds.activity)
    for o in act.observations:
        o.available_at = o.observed_at
    observations.extend(act.observations)
    observations = _resolve_aliases(observations, act.aliases)
    scopes = [o for o in observations if o.resource_id.startswith("scope:")]
    observations = [o for o in observations if not o.resource_id.startswith("scope:")]
    idx = EvidenceIndex(observations, act, captured).build()
    for sc in scopes:
        idx.scopes[sc.system_id].append(sc)
    idx.stats = {"observations": len(observations), "per_type": dict(per_type),
                 "ocr": dict(image_parser.STATS)}
    if verbose:
        print(f"[evidence] {len(observations)} observations from {len(ds.manifest)} files "
              f"{dict(per_type)}", file=sys.stderr)
    return idx
