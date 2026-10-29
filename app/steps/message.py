"""Step 9 - Draft the message to the service advisor.

Only findings marked Needs confirmation become questions. The model sees the
rule results (labels and job names), not the raw bill or any web text, and
its draft must pass the wording guard. Otherwise the deterministic template
below is used, so there is always a neutral message.
"""

from __future__ import annotations


from ..config import settings
from ..guard import accusatory_terms
from ..llm import ModelError, call_text, untrusted
from ..schemas import AuditReport, Finding

Q: dict[str, tuple[str, str]] = {
    "schedule_not_due": ("could you share the inspection finding? It isn't scheduled at this interval in the official service schedule.",
                         "क्या आप बता सकते हैं कि जाँच में क्या पाया गया? आधिकारिक सर्विस शेड्यूल में यह इस अंतराल पर निर्धारित नहीं है।"),
    "schedule_not_listed": ("could you explain why it's recommended and whether it's optional? It isn't listed in the official service schedule.",
                            "क्या आप बता सकते हैं कि यह क्यों सुझाया गया और क्या यह वैकल्पिक है? यह आधिकारिक सर्विस शेड्यूल में नहीं है।"),
    "schedule_inspect": ("could you share what the inspection found?",
                         "क्या आप बता सकते हैं कि जाँच में क्या पाया गया?"),
    "price": ("could you share the part's MRP and how the price was worked out?",
              "क्या आप इस पार्ट का MRP और इसकी कीमत कैसे तय हुई, यह बता सकते हैं?"),
    "labour": ("could you share the standard labour time and rate for this job?",
               "क्या आप इस काम का मानक लेबर समय और दर बता सकते हैं?"),
    "recall": ("could you check whether a manufacturer recall applies to my vehicle for this?",
               "क्या आप जाँच सकते हैं कि इसके लिए मेरी गाड़ी पर निर्माता का कोई रिकॉल लागू होता है?"),
    "warranty": ("could you check whether this can be done under warranty?",
                 "क्या आप जाँच सकते हैं कि यह काम वारंटी में हो सकता है?"),
    "auth_absent": ("could you confirm when this was approved? I don't see it on the estimate.",
                    "क्या आप बता सकते हैं कि इसकी मंज़ूरी कब ली गई थी? यह एस्टिमेट में नहीं दिखता।"),
    "auth_changed": ("could you explain the change from the amount I approved?",
                     "क्या आप मंज़ूर की गई राशि से हुए बदलाव के बारे में बता सकते हैं?"),
    "line_math": ("could you check the amount on this line? It doesn't match quantity × price.",
                  "क्या आप इस लाइन की राशि जाँच सकते हैं? यह मात्रा × कीमत से मेल नहीं खाती।"),
    "total_math": ("could you walk me through how the total was calculated?",
                   "क्या आप बता सकते हैं कि कुल राशि की गणना कैसे हुई?"),
}

OPEN = ("Hello, thank you for working on my vehicle. Before I pay, could you help me with a few items on the bill?",
        "नमस्ते, मेरी गाड़ी पर काम करने के लिए धन्यवाद। भुगतान से पहले, क्या आप बिल की कुछ चीज़ों के बारे में बता सकते हैं?")
CLOSE = ("Thank you.", "धन्यवाद।")
NOTHING = ("Hello, thank you for working on my vehicle. I've gone through the bill and have no questions.",
           "नमस्ते, मेरी गाड़ी पर काम करने के लिए धन्यवाद। मैंने बिल देख लिया है और मेरा कोई सवाल नहीं है।")


def question_key(f: Finding) -> str | None:
    if f.category != "needs_confirmation":
        return None
    lab = f.label.lower()
    if f.check == "schedule":
        if "inspection, not routine" in lab:
            return "schedule_inspect"
        if "not listed" in lab:
            return "schedule_not_listed"
        return "schedule_not_due"
    if f.check == "price":
        return "labour" if lab.startswith("labour") else "price"
    if f.check == "coverage":
        return "recall" if "recall" in lab else "warranty"
    if f.check == "authorization":
        return "auth_absent" if "not present" in lab else "auth_changed"
    if f.check == "arithmetic":
        return "line_math" if "line" in lab else "total_math"
    return None


def questions(report: AuditReport) -> list[tuple[str, str]]:
    """(item name, question key) pairs, one per distinct question."""
    out: list[tuple[str, str]] = []
    for lr in report.lines:
        name = lr.job_label if lr.item.job not in (None, "unrecognised") else (lr.item.raw_text.value or "this item")
        for f in lr.findings:
            k = question_key(f)
            if k and (name, k) not in out:
                out.append((name, k))
    for f in report.bill_findings:
        k = question_key(f)
        if k and ("The total", k) not in out:
            out.append(("The total", k))
    return out


def template_message(report: AuditReport, lang: str = "en") -> str:
    i = 1 if lang == "hi" else 0
    qs = questions(report)
    if not qs:
        return NOTHING[i]
    lines = [OPEN[i], ""]
    for n, (name, k) in enumerate(qs, 1):
        lines.append(f"{n}. {name}: {Q[k][i]}")
    lines += ["", CLOSE[i]]
    return "\n".join(lines)


def draft_message(report: AuditReport, lang: str = "en") -> tuple[str, str]:
    """Returns (message, source) where source is 'model' or 'template'."""
    template = template_message(report, lang)
    qs = questions(report)
    if not qs or not settings.models_configured:
        return template, "template"
    language = "simple, polite Hindi (Devanagari)" if lang == "hi" else "simple, polite English"
    msgs = [
        {"role": "system", "content":
            f"Rewrite the draft below as a short, warm WhatsApp message to a vehicle service advisor in {language}. "
            "Keep every numbered question and its meaning; add nothing new. Never accuse, never say overcharged, "
            "fraud, unnecessary or unauthorised, never mention complaints, courts or refunds. Plain text only."},
        {"role": "user", "content": untrusted("DRAFT MESSAGE", template)},
    ]
    try:
        text = call_text("message", settings.explain_model, msgs, max_tokens=700)
    except ModelError:
        return template, "template"
    if not text or accusatory_terms(text) or len(text) > 2000:
        return template, "template"
    return text.strip(), "model"
