"""Marketing-intelligence extraction (Media Plan v2, step 1) — evidence-BOUND.

Turns an Instagram/social SNAPSHOT (a list of `social_intel.SocialSignal`: captions/posts/comments
with their permalinks) into three grounded signals for the media plan: ACTIVE OFFERS, USPs, and
customer PAIN POINTS. Every extracted item MUST carry a VERBATIM quote from a real post/comment; the
CODE re-verifies the quote is an exact substring of the snapshot (the moat — PD-1), so an item the
model invents without a real quote is REJECTED. Zero hallucination by construction.

Governance: PARSE-ONLY over a snapshot the owner provides (PD-10 — no live IG scrape here). The
extractor takes `(signals, caller)`; hermetic with a MockCaller (zero spend). A live Gemini call is
authorized but careful (final verification only, logged).
"""
from .extractor import extract_marketing_intel, from_snapshot
from .schemas import MarketingIntelligence

__all__ = ["MarketingIntelligence", "extract_marketing_intel", "from_snapshot"]
