"""Step 8 - Old-part photo: supporting evidence only, never a verdict.

Returns looks_worn / looks_fine / cannot_determine plus what was visible.
The UI always shows it with the label "Photo observation: not proof that the
replacement was or wasn't needed". No rule reads this result.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..config import settings
from ..llm import call_json, image_part
from ..schemas import OldPartObservation


class _Out(BaseModel):
    result: Literal["looks_worn", "looks_fine", "cannot_determine"] = "cannot_determine"
    observed: str = ""


PROMPT = """You look at a photo of a vehicle part that a workshop says it removed and replaced.
Describe only what is visible. Answer "cannot_determine" whenever the angle, lighting, focus or
lack of scale means wear can't be judged (for example brake lining thickness without a reference).
Never say whether the replacement was needed. Return JSON:
{"result": "looks_worn" | "looks_fine" | "cannot_determine", "observed": "one or two plain sentences on what is visible"}"""


def observe(image: bytes, mime: str, part_name: str) -> OldPartObservation:
    msgs = [{"role": "system", "content": PROMPT},
            {"role": "user", "content": [image_part(image, mime), {"type": "text", "text": f"Part: {part_name}"}]}]
    out = call_json("old_part", settings.model_vision, msgs, _Out, max_tokens=400)
    return OldPartObservation(result=out.result, observed=out.observed[:300])
