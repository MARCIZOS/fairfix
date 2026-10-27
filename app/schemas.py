"""Pydantic schemas shared by the extraction steps, the rules and the API.

Design notes
- Every value read from a bill is a `Field` that remembers whether the model
  extracted it or the user corrected it, and keeps the original extraction.
- There are no fields for names, phone numbers, addresses or registration
  numbers: anything like that in a model's output is dropped by the schema.
- `Finding` is the audit trail: rule, inputs, evidence and uncertainty.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field as PField, model_validator

T = TypeVar("T")

Category = Literal["supported", "needs_confirmation", "unable_to_verify", "not_applicable"]
CATEGORY_LABELS: dict[str, str] = {
    "supported": "Supported by evidence",
    "needs_confirmation": "Needs confirmation",
    "unable_to_verify": "Unable to verify",
    "not_applicable": "Not applicable",
}

Check = Literal["schedule", "price", "coverage", "authorization", "arithmetic"]
LineKind = Literal["part", "labour", "consumable", "part_with_fitting", "unknown"]
Grade = Literal["genuine", "oem", "aftermarket", "unknown"]


class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Field(Loose, Generic[T]):
    """A value with provenance: extracted by the model or corrected by the user."""

    value: T | None = None
    origin: Literal["extracted", "user_corrected"] = "extracted"
    extracted_value: T | None = None

    @model_validator(mode="before")
    @classmethod
    def _wrap_plain(cls, v):
        """Accept a plain value as shorthand for an extracted field."""
        if v is None or not isinstance(v, dict):
            return {"value": v, "origin": "extracted", "extracted_value": v}
        return v


# --------------------------------------------------------------------------
# Documents read from photos
# --------------------------------------------------------------------------


class LineItem(Loose):
    raw_text: Field[str]
    qty: Field[float] = PField(default_factory=Field)
    unit_price_inr: Field[float] = PField(default_factory=Field)
    line_total_inr: Field[float] = PField(default_factory=Field)
    part_number: Field[str] = PField(default_factory=Field)
    gst_included: Field[bool] = PField(default_factory=Field)   # None = follow the document's setting
    kind: LineKind = "unknown"
    grade: Grade = "unknown"
    # Filled by the normalise step. The raw text is always kept beside it.
    job: str | None = None
    job_confidence: float | None = None
    job_source: Literal["alias", "model", "user", "none"] = "none"


class TaxLine(Loose):
    name: str                       # e.g. "CGST", "SGST", "IGST"
    rate_pct: float | None = None
    amount_inr: float | None = None


class Document(Loose):
    kind: Literal["bill", "estimate"] = "bill"
    doc_date: Field[str] = PField(default_factory=Field)         # ISO date if readable
    odometer_km: Field[float] = PField(default_factory=Field)
    job_card_no: Field[str] = PField(default_factory=Field)
    lines: list[LineItem] = PField(default_factory=list)
    subtotal_inr: Field[float] = PField(default_factory=Field)
    discount_inr: Field[float] = PField(default_factory=Field)
    discount_applied: Field[Literal["before_tax", "after_tax"]] = PField(default_factory=Field)
    prices_include_tax: Field[bool] = PField(default_factory=Field)
    tax_lines: list[TaxLine] = PField(default_factory=list)
    round_off_inr: Field[float] = PField(default_factory=Field)
    grand_total_inr: Field[float] = PField(default_factory=Field)
    extraction_notes: list[str] = PField(default_factory=list)


# --------------------------------------------------------------------------
# Inputs the user provides
# --------------------------------------------------------------------------


class JobHistory(Loose):
    """When a scheduled item was last replaced, if the owner knows."""

    job: str
    status: Literal["replaced", "never", "unknown"] = "unknown"
    km: float | None = None
    on: date | None = None


class Vehicle(Loose):
    vehicle_id: str | None = None     # key into the fact pack, e.g. "honda_activa_6g"
    make: str
    model: str
    variant: str | None = None
    fuel: str | None = None
    odometer_km: float | None = None
    purchase_date: date | None = None
    registration_date: date | None = None
    mfg_month: str | None = None      # "YYYY-MM"
    city: str | None = None
    severe_use: bool = False
    history: list[JobHistory] = PField(default_factory=list)


class ApprovalNote(Loose):
    """A later approval the owner records in writing (no screenshots)."""

    on: date | None = None
    item: str
    job: str | None = None
    how: str = ""                     # e.g. "phone call with advisor"
    amount_inr: float | None = None


# --------------------------------------------------------------------------
# Reference facts
# --------------------------------------------------------------------------


class Fact(Loose):
    kind: Literal["schedule", "warranty", "recall", "part_price", "labour_price"]
    applies_to: dict[str, Any] = PField(default_factory=dict)
    data: dict[str, Any] = PField(default_factory=dict)
    source_url: str = ""
    excerpt: str = ""
    checked_on: date | None = None
    source_rank: int = 3             # 1 manufacturer/official, 2 reputable seller or publication, 3 community
    sufficient: bool = False         # set by the rules for this check, never by a model
    verified_by_human: bool = False
    from_cache: bool = False


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


class Finding(Loose):
    check: Check
    category: Category
    label: str
    rule: str
    inputs: dict[str, Any] = PField(default_factory=dict)
    evidence: list[Fact] = PField(default_factory=list)
    uncertainty: str | None = None
    next_step: str = ""
    verify_url: str | None = None
    calculation: str | None = None

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]


class OldPartObservation(Loose):
    result: Literal["looks_worn", "looks_fine", "cannot_determine"]
    observed: str = ""
    label: str = "Photo observation: not proof that the replacement was or wasn't needed"


class LineResult(Loose):
    index: int
    item: LineItem
    job_label: str
    findings: list[Finding]


class AuditReport(Loose):
    lines: list[LineResult]
    bill_findings: list[Finding]
    old_part: OldPartObservation | None = None
    needs_confirmation_total_inr: float = 0
    partial_audit: bool = False
    partial_reason: str | None = None
    message_to_advisor: str = ""
    message_language: str = "en"
    message_source: Literal["model", "template"] = "template"
    disclaimer: str = "A second opinion from public information, not a mechanic's inspection or legal advice."
    warnings: list[str] = PField(default_factory=list)
