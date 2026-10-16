"""Deterministic material-distinctness checks for positioning alternatives."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping, Sequence


_COMPARISON_FIELDS = (
    "target",
    "frame_of_reference",
    "consumer_value",
    "differentiated_value",
    "reason_to_believe",
)

_STOP_WORDS = {
    "a", "an", "and", "as", "at", "be", "because", "by", "customer", "customers",
    "diner", "diners", "for", "from", "in", "is", "it", "of", "on", "people", "that",
    "the", "their", "this", "through", "to", "user", "users", "with",
}

# These families are deliberately category- and country-neutral. They canonicalize wording; they
# never add a strategic premise or decide that an unevidenced option is acceptable.
_ALIASES = {
    "application": "app",
    "applications": "app",
    "convenient": "convenience",
    "easy": "convenience",
    "easier": "convenience",
    "easiest": "convenience",
    "frictionless": "convenience",
    "seamless": "convenience",
    "customised": "personalization",
    "customized": "personalization",
    "personalised": "personalization",
    "personalized": "personalization",
    "tailored": "personalization",
    "deal": "offer",
    "deals": "offer",
    "discount": "offer",
    "discounts": "offer",
    "promotion": "offer",
    "promotions": "offer",
    "offers": "offer",
    "membership": "loyalty",
    "memberships": "loyalty",
    "point": "loyalty",
    "points": "loyalty",
    "reward": "loyalty",
    "rewards": "loyalty",
    "quick": "speed",
    "quickly": "speed",
    "fast": "speed",
    "faster": "speed",
    "seeker": "seek",
    "seekers": "seek",
    "seeking": "seek",
    "tries": "trial",
    "try": "trial",
    "trying": "trial",
}


def _field(option: Any, name: str) -> str:
    raw = option.get(name) if isinstance(option, Mapping) else getattr(option, name, "")
    return str(raw or "").strip()


def normalize_material(value: Any) -> str:
    """Normalize presentation differences without translating or inventing semantics."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"\w+", text, flags=re.UNICODE))


def _stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ers", "ed"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def canonical_terms(value: Any) -> frozenset[str]:
    terms: set[str] = set()
    for raw in normalize_material(value).split():
        if raw in _STOP_WORDS:
            continue
        canonical = _ALIASES.get(raw, _stem(raw))
        canonical = _ALIASES.get(canonical, canonical)
        if canonical and canonical not in _STOP_WORDS:
            terms.add(canonical)
    return frozenset(terms)


def semantic_overlap(left: Any, right: Any) -> int:
    """Count shared canonical concepts; zero means there is no deterministic semantic link."""
    return len(canonical_terms(left) & canonical_terms(right))


def materially_equivalent_text(left: Any, right: Any) -> bool:
    """Catch exact and high-confidence paraphrase equivalence without an LLM."""
    left_normal = normalize_material(left)
    right_normal = normalize_material(right)
    if not left_normal or not right_normal:
        return left_normal == right_normal
    if left_normal == right_normal:
        return True
    left_terms = canonical_terms(left)
    right_terms = canonical_terms(right)
    if not left_terms or not right_terms:
        return False
    if left_terms == right_terms:
        return True
    intersection = len(left_terms & right_terms)
    union = len(left_terms | right_terms)
    overlap = intersection / min(len(left_terms), len(right_terms))
    jaccard = intersection / union
    return overlap >= 0.86 and jaccard >= 0.80


@dataclass(frozen=True, slots=True)
class AlternativeComparison:
    left_option_id: str
    right_option_id: str
    equivalent_fields: tuple[str, ...]
    differing_fields: tuple[str, ...]
    materially_duplicate: bool
    reason: str


def compare_positioning_options(left: Any, right: Any) -> AlternativeComparison:
    equivalent = tuple(
        name for name in _COMPARISON_FIELDS
        if materially_equivalent_text(_field(left, name), _field(right, name))
    )
    differing = tuple(name for name in _COMPARISON_FIELDS if name not in equivalent)
    duplicate = not differing
    reason = (
        "All substantive positioning fields are equivalent after normalized structural and "
        "semantic comparison; wording, IDs, and numbered trade-offs do not create a new basis "
        "of competition."
        if duplicate else
        "The alternatives make a substantive choice in: " + ", ".join(differing) + "."
    )
    return AlternativeComparison(
        left_option_id=_field(left, "option_id"),
        right_option_id=_field(right, "option_id"),
        equivalent_fields=equivalent,
        differing_fields=differing,
        materially_duplicate=duplicate,
        reason=reason,
    )


def duplicate_positioning_pairs(options: Sequence[Any]) -> tuple[AlternativeComparison, ...]:
    duplicates: list[AlternativeComparison] = []
    for right_index, right in enumerate(options):
        for left in options[:right_index]:
            comparison = compare_positioning_options(left, right)
            if comparison.materially_duplicate:
                duplicates.append(comparison)
    return tuple(duplicates)


__all__ = [
    "AlternativeComparison", "canonical_terms", "compare_positioning_options",
    "duplicate_positioning_pairs", "materially_equivalent_text", "normalize_material",
    "semantic_overlap",
]
