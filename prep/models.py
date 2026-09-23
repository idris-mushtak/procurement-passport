"""Pydantic contracts. These mirror the Supabase tables one-for-one, so a
validated model can be emitted as SQL without further massaging."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ReqType = Literal[
    "turnover", "certification", "references",
    "insurance", "staff_language", "local_presence", "other",
]
Operator = Literal["gte", "lte", "eq", "exists", "count_gte"]
Trust = Literal["verified", "official", "claim"]
Confidence = Literal["high", "medium", "low"]


class Tender(BaseModel):
    id: str
    ted_id: str
    title: str
    buyer: str
    value_eur: Optional[float] = None
    deadline: Optional[date] = None
    cpv: list[str] = Field(default_factory=list)
    joint_bids_allowed: bool = True
    subcontracting_allowed: bool = True
    portal: Optional[str] = None
    documents_url: Optional[str] = None
    source_url: str
    pack_status: str = "none"  # none | fetched | unavailable
    notes: Optional[str] = None


class Requirement(BaseModel):
    """One extracted knock-out requirement. Every row must cite its clause."""
    tender_id: str
    req_type: ReqType
    key: str
    operator: Operator
    threshold_num: Optional[float] = None
    threshold_text: Optional[str] = None
    window_years: Optional[int] = None
    public_sector_required: bool = False
    knockout: bool = True
    lot: Optional[str] = None
    clause_ref: Optional[str] = None
    source_quote: str
    page: Optional[int] = None
    confidence: Confidence = "medium"
    source_doc: Optional[str] = None

    @field_validator("source_quote")
    @classmethod
    def _quote_is_real(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 25:
            raise ValueError("source_quote too short to be a real clause")
        return v[:1200]

    @field_validator("key")
    @classmethod
    def _key_slug(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_").replace("-", "_")

    @model_validator(mode="after")
    def _threshold_matches_operator(self) -> "Requirement":
        numeric = {"gte", "lte", "count_gte"}
        if self.operator in numeric and self.threshold_num is None:
            raise ValueError(f"operator {self.operator} needs threshold_num")
        if self.operator == "exists" and self.threshold_num is not None:
            self.threshold_num = None
        # A judgement-call requirement must never be auto-checkable.
        if self.req_type == "other":
            self.knockout = False
        return self


class Capability(BaseModel):
    company_id: str
    req_type: ReqType
    key: str
    value_num: Optional[float] = None
    value_text: Optional[str] = None
    valid_until: Optional[date] = None
    trust: Trust = "claim"
    source_url: Optional[str] = None
    source_quote: Optional[str] = None


class Reference(BaseModel):
    company_id: str
    buyer: str
    public_sector: bool = True
    cpv: Optional[str] = None
    value_eur: Optional[float] = None
    end_date: Optional[date] = None
    trust: Trust = "official"
    source_url: Optional[str] = None
    title: Optional[str] = None


class Company(BaseModel):
    id: str
    name: str
    is_demo: bool = False
    country: str = "NL"
    prefs: dict = Field(default_factory=dict)


class ExtractionBatch(BaseModel):
    """What the model is asked to return for one tender."""
    requirements: list[Requirement]
    joint_bids_allowed: Optional[bool] = None
    subcontracting_allowed: Optional[bool] = None
