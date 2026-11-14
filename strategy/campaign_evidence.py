"""Capture scoped publisher text and distinguish quoted material from usable copy."""
from dataclasses import dataclass
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


QUALIFICATION = re.compile(
    r"[$€£]|\b(?:only|except|until|unless|eligible|subject to|"
    r"requires?|paid subscription|disabled by default|"
    r"available (?:on|from|through|to)|(?:must|need to) (?:enable|activate|subscribe|pay)|"
    r"when (?:enabled|activated)|valid (?:until|through)|no longer)\b"
    r"|\b(?:فقط|حتى|سعر|خصم|عرض|فرع|يشترط|يتطلب|تتطلب|تسري|بشرط|حصريًا|مدفوع)\b",
    re.IGNORECASE,
)
CONFLICT_SIGNAL = re.compile(
    r"\b(?:no longer|discontinued|not available|unavailable|does not support|temporarily suspended)\b"
    r"|لم يعد|غير متاح|توقف دعم", re.IGNORECASE,
)
REFERENCE = re.compile(
    r"^(?:it\b|this\b|these\b|the (?:feature|service|product|pass|offer|device)\b"
    r"|هذه\b|هذا\b|تسري\b|يتطلب\b|تتطلب\b|يشترط\b)", re.IGNORECASE,
)
QUALIFIER_LABEL = re.compile(
    r"^(?:eligibility|requirements?|compatibility|limitations?|conditions?|availability|"
    r"validity|pricing|notes?|الشروط|المتطلبات|الإتاحة|الصلاحية|ملاحظات)\b", re.IGNORECASE,
)
INSTRUCTION = re.compile(
    r"\b(?:ignore (?:all |the |previous |prior )?(?:instructions|(?:source )?rules)|"
    r"system prompt|you are (?:an? |the )?(?:ai|assistant)|output exactly)\b"
    r"|تجاهل التعليمات|تعليمات النظام", re.IGNORECASE,
)


class TextSpan(BaseModel):
    block: int = Field(ge=0)
    element: Literal["p", "li", "dt", "dd", "tr", "blockquote"]
    text: str = Field(min_length=1, max_length=1600)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str

    @model_validator(mode="after")
    def verify_span(self):
        if not self.start < self.end <= len(self.text) or self.text[self.start:self.end] != self.quote:
            raise ValueError("Quote does not match its normalized source block")
        return self


class EvidenceAnchor(BaseModel):
    subject: str
    support: TextSpan
    qualifications: tuple[TextSpan, ...] = ()
    context_extent: Literal["BOUNDED_SECTION_NEIGHBORHOOD"] = "BOUNDED_SECTION_NEIGHBORHOOD"


@dataclass(frozen=True)
class SourceBlock:
    ordinal: int
    element: str
    text: str
    context_only: bool = False

    def span(self, start=0, end=None):
        end = len(self.text) if end is None else end
        return TextSpan(block=self.ordinal, element=self.element, text=self.text,
                        start=start, end=end, quote=self.text[start:end])


def anchor_for(subject, group, index, start, end, subject_tokens):
    support = group[index].span(start, end)
    subject_words = subject_tokens(subject)
    qualifications = []
    for other in group[max(0, index - 2):index + 3]:
        if not 0 < len(other.text) <= 1600 or INSTRUCTION.search(other.text):
            continue
        if not QUALIFICATION.search(other.text) and not CONFLICT_SIGNAL.search(other.text):
            continue
        # Qualifiers in a separate block must point back to the topic or appear in
        # a structured requirements row/item. A peer heading starts a new group.
        overlap = subject_tokens(other.text) & subject_words
        if (other.ordinal == support.block or other.context_only or REFERENCE.match(other.text)
                or QUALIFIER_LABEL.match(other.text) or len(overlap) >= min(2, max(1, len(subject_words)))):
            qualifications.append(other.span())
    return EvidenceAnchor(subject=subject, support=support, qualifications=tuple(qualifications))
