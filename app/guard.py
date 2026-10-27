"""Wording guard: FairFix asks questions, it never accuses.

Any user-facing text that a model writes (the message to the advisor) is
checked here. If it contains accusatory wording, the app falls back to the
deterministic template message. The eval counts every hit as an
"unsupported accusation", which must be zero to release.
"""

from __future__ import annotations

import re

# Stems, matched case-insensitively at word starts (English and common Hinglish/Hindi).
ACCUSATORY_STEMS: tuple[str, ...] = (
    "fraud", "scam", "cheat", "rip off", "ripoff", "rip-off", "loot", "steal", "stole", "theft",
    "overcharg", "over-charg", "over charg", "unauthori", "unauthorised", "unnecessar", "unneeded",
    "illegal", "fake", "dishonest", "con job", "swindl", "extort", "lied", "lying", "liar",
    "you must refund", "consumer court", "legal action", "police",
    "धोखा", "ठगी", "ठग", "लूट", "फर्जी", "गैरकानूनी", "बेईमान",
)

_PATTERNS = [re.compile(r"(?<![a-z])" + re.escape(s), re.IGNORECASE) for s in ACCUSATORY_STEMS]


def accusatory_terms(text: str) -> list[str]:
    """Return the accusatory stems found in `text` (empty list = clean)."""
    found = []
    for stem, pat in zip(ACCUSATORY_STEMS, _PATTERNS):
        if pat.search(text or ""):
            found.append(stem)
    return found


def is_neutral(text: str) -> bool:
    return not accusatory_terms(text)
