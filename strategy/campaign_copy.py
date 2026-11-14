"""A small, reviewable vocabulary for claims supported by public publisher text."""
from dataclasses import dataclass
import re
import unicodedata

from pydantic import Field

from strategy.campaign_package import Input
from strategy.campaign_v2 import CampaignConflict


CTA_MAX_LENGTH = 30


class CopyEdit(Input):
    headline: str = Field(min_length=3, max_length=55)
    message: str = Field(min_length=4, max_length=105)
    cta: str = Field(min_length=3, max_length=CTA_MAX_LENGTH)
    caption: str = Field(min_length=4, max_length=220)


def normalized(value):
    return " ".join(re.sub(r"[^\w ]", " ", unicodedata.normalize("NFKC", value).lower()).split())


@dataclass(frozen=True)
class Writing:
    concept: str
    headline: str
    message: str
    cta: str
    caption: str
    alternate_messages: tuple[str, ...] = ()

    def license(self, field, value):
        key = normalized(value)
        if key == normalized(getattr(self, field)):
            return True
        if field == "message" and key in {normalized(item) for item in self.alternate_messages}:
            return True
        if self.concept == "compare" and "zoom" in self.message.lower():
            if field == "headline":
                return key == "compare artworks side by side"
            if field == "message":
                return bool(re.fullmatch(
                    r"use the comparator to place artworks (?:next to each other|side by side) and zoom in on the smallest details",
                    key,
                ))
        return False


def writing_for(fact, action, language):
    quote = fact["quote"]
    context = fact.get("context", quote)
    if fact.get("anchor", {}).get("qualifications") or re.search(r"\b(?:only|except|until|some regions|eligible|when enabled|if enabled|does not|no longer)\b|[$€£]", context, re.I):
        return None
    ar = language.split("-")[0] == "ar"
    if re.fullmatch(r"[\w® .-]{2,48} blocks thousands of known trackers (?:before they can follow you around the web|while you browse(?: the web)?)\.", quote, re.I) and not re.search(
        r"\b(?:never|not|cannot|claims|said|could|would|might|previously)\b", quote, re.I
    ):
        if action == "DOWNLOAD_OPTIONS":
            return Writing("filter", "متعقّبات معروفة خارج المسار" if ar else "Keep known trackers out",
                "يحظر المتصفّح آلاف المتعقّبات المعروفة." if ar else "The browser blocks thousands of known trackers.",
                "اعرض خيارات التنزيل" if ar else "View download options",
                "يحظر المتصفّح آلاف المتعقّبات المعروفة. اعرض خيارات التنزيل من موقع الناشر." if ar else
                "The browser blocks thousands of known trackers. View download options from the publisher.")
        return Writing("filter", "ماذا يحظر متصفّحك؟" if ar else "Which trackers get blocked?",
            "يحظر المتصفّح آلاف المتعقّبات المعروفة." if ar else "The browser blocks thousands of known trackers.",
            "اقرأ عن حظر المتعقّبات" if ar else "Read about tracker blocking",
            "يحظر المتصفّح آلاف المتعقّبات المعروفة أثناء تصفّح الويب. اقرأ كيف تعمل الميزة." if ar else
            "The browser blocks thousands of known trackers as you browse. Read how the feature works.",
            ("يحجب المتصفّح آلاف المتعقّبات المعروفة أثناء التصفّح." if ar else
             "The browser stops thousands of known trackers while you browse.",))
    path = fact.get("page_url", "").lower()
    catalog = bool(re.search(r"\b(?:catalog|shop|pieces|brick)\b", fact.get("title", "") + " " + path.replace("-", " "), re.I))
    if catalog and action != "DOWNLOAD_OPTIONS" and re.search(r"\b(?:piece|brick)s?\b", quote, re.I) and re.search(
        r"\b(?:browse|browsing|searching for|finding)\b", quote, re.I
    ):
        return Writing("assemble", "مشروعك ناقص قطعة؟" if ar else "One piece left to find?",
            "ابحث عن قطعة لمشروعك في الكتالوج." if ar else "Browse the catalog for a piece for your build.",
            "تصفّح القطع" if ar else "Browse the pieces",
            "بتكمّل مشروع بناء؟ تصفّح الكتالوج وابحث عن القطعة اللي محتاجها." if ar else
            "Finishing a build? Explore the catalog and look for the piece you need.")
    if action != "DOWNLOAD_OPTIONS" and re.fullmatch(
        r"(?:Use the Comparator to place artworks side by side(?: and zoom in on the smallest details)?|Compare artworks side by side)\.?",
        quote, re.I,
    ):
        zoom = bool(re.search(r"\bzoom in\b", quote, re.I))
        return Writing("compare", "قارن عملين جنبًا إلى جنب" if ar else "Two artworks. Side by side.",
            ("قارن الأعمال وكبّر التفاصيل." if zoom else "قارن الأعمال جنبًا إلى جنب.") if ar else
            ("Compare artworks and zoom into details." if zoom else "Compare artworks side by side."),
            "اقرأ عن أداة المقارنة" if ar else "Read about the Comparator",
            ("شوف إيه اللي يبان لما تحط عملين جنب بعض. اقرأ عن أداة المقارنة." if ar else
             "What changes when you look at two artworks together? Read about the Comparator."))
    return None


def plan_copy(fact, options, language):
    return [writing_for(fact, option["action"], language) for option in options]


def source_read_cta(label, language):
    prefix = "اقرأ عن" if language.split("-")[0] == "ar" else "Explore"
    text = f"{prefix} {label}"
    if len(text) <= CTA_MAX_LENGTH:
        return text


def validate_copy(direction, edit, writing, *, source_heading=None):
    """Only the bounded, source-licensed wording can enter an automated package."""
    fields = direction.model_dump()
    for key, value in edit.model_dump().items():
        permitted = [fields[key]]
        if key == "headline" and source_heading:
            permitted.append(source_heading)
        if value not in permitted and not (writing and writing.license(key, value)):
            raise CampaignConflict("COPY_REQUIRES_EVIDENCE_REVIEW")
    return direction.model_copy(update={**edit.model_dump(), "hook": edit.headline})
