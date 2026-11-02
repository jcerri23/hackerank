"""Step 1 - Does the request text authorise deletion?

Two layers:
  * a multilingual rule layer (keywords for deletion and for the non-deletion intents
    named in the problem statement: access, correction, export, marketing opt-out, login help);
  * an optional neural layer: a multilingual sentence-embedding model
    (sentence-transformers, default `paraphrase-multilingual-MiniLM-L12-v2`) that compares
    the request with English prototype sentences of each intent.  It runs fully locally, is
    only consulted when the rules are not confident, and is skipped if the library or the
    model weights are not available (set GHOST_INTENT_MODEL to change the model, or
    GHOST_DISABLE_NEURAL=1 to turn it off).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

DELETION_PATTERNS = [
    r"\bdelet(e|ed|ing|ion)\b", r"\berase(d)?\b", r"\berasure\b", r"\bremove my (account|profile)\b",
    r"\bclose (my|this) account\b", r"\bclose the account\b", r"account[- ]deletion",
    r"\bsuppression\b", r"\bsupprimer\b", r"\beliminaci[oó]n\b", r"\beliminar\b", r"\bexclus[aã]o\b",
    r"\bexcluir\b", r"\bl[oö]schung\b", r"\bl[oö]schen\b", r"\bcancellazione\b", r"\bverwijder",
    "削除", "삭제", "删除", "消去", r"\busuni",
]
NON_DELETION_PATTERNS = [
    r"\b(mobile|phone) number\b.*\b(out of date|update|change)", r"\bpromotional emails?\b",
    r"\bmarketing (lists?|emails?)\b", r"\bunsubscribe\b", r"\bopt[- ]?out\b",
    r"\bdownload my (account )?(information|data)\b", r"\bportable format\b",
    r"\b(reset|recover) (the |my )?(login|password)\b", r"\bcan'?t access my profile\b",
    r"\bmisspelled\b", r"\bcorrect(ion)? (of )?my (name|surname)\b",
    r"\bwhat personal (information|data) you hold\b", r"\bcopy of (the |my )?(information|data|invoices?)\b",
    r"\binvoices?\b.*\bfor my records\b", r"\bchange the email address\b", r"\bupdate (my|the) email\b",
    r"\bsend me a copy\b",
]
NEGATED_DELETION = [r"\bonly\b.*\bmarketing\b", r"\bmarketing lists only\b",
                    r"\b(do not|don'?t|please don'?t) (delete|close|remove)\b", r"\bnot asking for (deletion|removal)\b",
                    r"\bnot a request to delete\b", r"\bkeep (my|the) account\b", r"\bleave the account active\b",
                    r"\bwithout removing\b", r"\bcontinue using the service\b"]
# softer deletion cues used by follow-up messages ("my old profile still appears, remove it")
SOFT_DELETION_PATTERNS = [r"\bremov(e|ed|al)\b", r"\b(account|profile)\b.*\b(closed|gone)\b",
                          r"\bclosed?\b.*\baccount\b", r"\bwipe\b"]

PROTOTYPES = {
    True: [
        "Please delete my account and all my personal data.",
        "I want my account permanently erased.",
        "Close my account and remove my personal information.",
    ],
    False: [
        "Please update the phone number on my profile.",
        "Unsubscribe me from marketing emails.",
        "Send me a copy of the personal data you hold about me.",
        "I need help resetting my password.",
        "Please change the email address on my account.",
        "I need copies of my invoices.",
    ],
}


@dataclass
class IntentDecision:
    is_deletion: bool
    confidence: float
    method: str          # rules | neural | default
    rationale: str


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


class _NeuralIntent:
    """Lazy multilingual embedding classifier (nearest prototype, cosine similarity)."""

    def __init__(self):
        self.model = None
        self.available = None
        self.calls = 0
        self.name = os.environ.get("GHOST_INTENT_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    def _load(self):
        if self.available is not None:
            return self.available
        if os.environ.get("GHOST_DISABLE_NEURAL") == "1":
            self.available = False
            return False
        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(self.name, device="cpu")
            self.proto = {k: self.model.encode(v, normalize_embeddings=True) for k, v in PROTOTYPES.items()}
            self.available = True
        except Exception:
            self.available = False
        return self.available

    def predict(self, text: str):
        if not self._load():
            return None
        import numpy as np

        self.calls += 1
        v = self.model.encode([text], normalize_embeddings=True)[0]
        scores = {k: float(np.max(p @ v)) for k, p in self.proto.items()}
        label = scores[True] >= scores[False]
        margin = abs(scores[True] - scores[False])
        return label, min(0.99, 0.5 + margin), scores


NEURAL = _NeuralIntent()


def classify(text: str) -> IntentDecision:
    t = _norm(text)
    pos = [p for p in DELETION_PATTERNS if re.search(p, t)]
    neg = [p for p in NON_DELETION_PATTERNS if re.search(p, t)]
    negated = [p for p in NEGATED_DELETION if re.search(p, t)]
    if neg or negated:
        strong_pos = [p for p in pos if "account" in t or "compte" in t or "cuenta" in t or "conta" in t]
        if not strong_pos or negated:
            return IntentDecision(False, 0.95, "rules", f"non-deletion cue: {(neg or negated)[0]}")
    if pos:
        return IntentDecision(True, 0.95, "rules", f"deletion cue: {pos[0]}")
    neural = NEURAL.predict(text)
    soft = [p for p in SOFT_DELETION_PATTERNS if re.search(p, t)]
    if neural is not None:
        label, conf, scores = neural
        if conf >= 0.6 or not soft:
            return IntentDecision(label, conf, "neural", f"embedding scores {scores}")
    if soft:
        return IntentDecision(True, 0.7, "rules", f"soft deletion cue: {soft[0]}")
    return IntentDecision(False, 0.5, "default", "no deletion cue found")
