"""Compile a review package from an authored brief and observed public evidence."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Annotated, Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scraper.url_utils import same_registrable_host
from scraper.campaign_http import valid_source_url
from strategy.campaign_research import ResearchGap, load_public_page, same_source
from strategy.campaign_v2 import CampaignConflict


Label = Annotated[str, StringConstraints(min_length=2, max_length=75)]
Line = Annotated[str, StringConstraints(min_length=4, max_length=220)]
Note = Annotated[str, StringConstraints(min_length=4, max_length=550)]
Theme = Literal["ember", "cobalt", "forest"]
FRESH_FOR = timedelta(days=7)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(Input):
    url: Annotated[str, StringConstraints(max_length=500)]
    quote: Annotated[str, StringConstraints(min_length=6, max_length=180)]
    scope: Line


class Alternative(Input):
    name: Label
    plan: Line
    tradeoff: Line


class Direction(Input):
    name: Label
    rationale: Line
    headline: Annotated[str, StringConstraints(min_length=3, max_length=55)]
    message: Annotated[str, StringConstraints(min_length=4, max_length=105)]
    cta: Annotated[str, StringConstraints(min_length=3, max_length=30)]
    theme: Theme
    caption: Note
    hook: Line
    shot: Line
    middle_shot: Line
    end_shot: Line


class Brief(Input):
    summary: Note
    audience: Line
    market: Line
    competitor: Label | None = None
    peer_rationale: Line | None = None
    source: Source
    peer_source: Source | None = None
    objective: Line
    metric: Line
    assumption: Note
    alternatives: Annotated[tuple[Alternative, ...], Field(min_length=2, max_length=3)]
    strategy_index: int = Field(ge=0, le=2)
    strategy_reason: Note
    tone: Label
    directions: Annotated[tuple[Direction, ...], Field(min_length=2, max_length=3)]
    selected: int = Field(ge=0, le=2)
    human_review: Annotated[list[Note], Field(min_length=1, max_length=6)]
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")]
    direction: Literal["rtl", "ltr"]

    @model_validator(mode="after")
    def check_choices(self):
        if len(self.directions) != len(self.alternatives) or max(self.selected, self.strategy_index) >= len(self.directions):
            raise ValueError("Invalid direction selection")
        return self


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def validate_sources(brief, brand_url):
    if brief.peer_source is None or not brief.competitor or not brief.peer_rationale:
        raise CampaignConflict("PEER_SOURCE_REQUIRED")
    for url in (brief.source.url, brief.peer_source.url):
        if not valid_source_url(url):
            raise CampaignConflict("INVALID_SOURCE_URL")
    if not same_source(brief.source.url, brand_url):
        raise CampaignConflict("BRAND_SOURCE_ORIGIN_MISMATCH")
    if same_registrable_host(brief.peer_source.url, brand_url):
        raise CampaignConflict("PEER_SOURCE_MUST_BE_DISTINCT")
    if len({item.name.casefold() for item in brief.directions}) != len(brief.directions):
        raise CampaignConflict("DIRECTIONS_MUST_BE_DISTINCT")
    if len({item.name.casefold() for item in brief.alternatives}) != len(brief.alternatives):
        raise CampaignConflict("STRATEGIES_MUST_BE_DISTINCT")


def public_soup(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select("script,style,template,noscript,svg,[hidden],[aria-hidden=true],[style]"):
        if not tag.name:
            continue
        if tag.has_attr("style") and not tag.has_attr("hidden") and tag.get("aria-hidden") != "true":
            hidden = {"display:none", "visibility:hidden", "visibility:collapse", "opacity:0"}
            rules = {"".join(rule.lower().split()).removesuffix("!important")
                     for rule in tag["style"].split(";")}
            if tag.name not in {"script", "style", "template", "noscript", "svg"} and not hidden & rules:
                continue
        if tag.parent is not None:
            tag.decompose()
    return soup


def capture(source, load, clock):
    trace = {"page_url": source.url}
    try:
        page = load(source.url, 5.0)
        if page.url != source.url:
            raise ResearchGap("SOURCE_URL_CHANGED")
        soup = public_soup(page.body)
        body = soup.body
        if body is None:
            raise ResearchGap("SOURCE_TEXT_MISSING")
        quote = " ".join(source.quote.split())
        blocks = [" ".join(tag.get_text(" ", strip=True).split())
                  for tag in body.select("h1,h2,h3,h4,p,li,dt,dd,blockquote")]
        matches = {block for block in blocks if quote in block and len(block) <= 1600}
        if len(matches) != 1:
            raise ResearchGap("QUOTE_NOT_FOUND" if not matches else "QUOTE_CONTEXT_AMBIGUOUS")
        return {**trace, "outcome": "QUOTE_OBSERVED", "quote": quote, "scope": source.scope,
                "context": matches.pop(), "capture_kind": page.source_kind,
                "captured_at": page.captured_at or clock().isoformat(),
                "body_sha256": sha256(page.body).hexdigest(),
                "text_sha256": digest(" ".join(body.get_text(" ", strip=True).split()))}
    except (OSError, ResearchGap) as exc:
        return {**trace, "outcome": str(exc) if isinstance(exc, ResearchGap) else "SOURCE_UNAVAILABLE",
                "captured_at": clock().isoformat()}


def load_package_page(url, timeout):
    return load_public_page(url, timeout, max_bytes=2_000_000, user_agent="ADZY/1.0", socket_timeout=3.0)


def compile_package(brief, brand_name, sources):
    selected = brief.directions[brief.selected]
    direction = selected.model_dump()
    source = sources[0]
    return {
        "brand_name": brand_name,
        "language": brief.language,
        "direction": brief.direction,
        "source_url": source["page_url"],
        "source_evidence_index": 0,
        "factual_review": "AUTHORED_COPY_REQUIRES_HUMAN_REVIEW",
        "copy": {key: direction[key] for key in ("headline", "message", "cta")},
        "theme": selected.theme,
        "caption": selected.caption,
        "storyboard": [
            {"seconds": "0–3", "visual": selected.shot, "line": selected.hook},
            {"seconds": "3–8", "visual": selected.middle_shot, "line": selected.message},
            {"seconds": "8–12", "visual": selected.end_shot, "line": selected.cta},
        ],
        "poster_layout": "source-campaign@1",
    }


def proposal(brief, brand_name, sources, *, changes=(), planner=None):
    ready = bool(brief and sources and all(row["outcome"] == "QUOTE_OBSERVED" for row in sources))
    status = "READY" if ready else "BLOCKED"
    if planner is not None and ready and not planner["brief_reviewed"]:
        status = "DRAFT"
    package = compile_package(brief, brand_name, sources) if status == "READY" else None
    if package is not None and planner is not None:
        choice = planner["options"][brief.selected]
        package.update(source_url=choice["destination"], source_evidence_index=choice["evidence_index"],
                       factual_review="SOURCE_LABEL_REUSE_REQUIRES_HUMAN_REVIEW")
        if planner.get("qualified_source_guide"):
            package["factual_review"] = "QUALIFIED_SOURCE_GUIDE"
        if planner.get("edition"):
            art = choice["art"]
            layout = {
                "scene-comparison": "source-campaign@5",
                "ingredient-columns": "source-campaign@4",
                "ingredient-rows": "source-campaign@4",
            }.get(art.get("layout"), "source-campaign@3")
            package.update(
                poster_layout=layout, art=art,
                factual_review="EXACT_SOURCE_ANCHORS_AND_EDITORIAL_REVIEW",
                claims=choice["claims"],
            )
            package.pop("storyboard", None)
        elif choice.get("concept"):
            package.update(concept=choice["concept"], poster_layout="source-campaign@2",
                           factual_review="BOUNDED_REWRITE_REQUIRES_HUMAN_REVIEW")
        planner = {**planner, "destination_evidence_index": choice["evidence_index"]}
    result = {
        "schema_version": 2, "kind": "SOURCE_CAMPAIGN", "brand_name": brand_name,
        "brief": brief.model_dump(mode="json") if brief else None, "evidence": sources,
        "status": status, "package": package,
        "changes": list(changes),
    }
    if planner is not None:
        result["planner"] = planner
    return {**result, "sha256": digest(result)}


def recheck_at(result):
    return min(datetime.fromisoformat(row["captured_at"]) for row in result["evidence"]) + FRESH_FOR


def check(result, now):
    if result.get("sha256") != digest({k: v for k, v in result.items() if k != "sha256"}):
        return "CREATIVE_BINDING_CHANGED"
    if result["status"] == "BLOCKED":
        return "EVIDENCE_GAP"
    planner = result.get("planner")
    if planner and not planner.get("edition") and not result["evidence"][0].get("anchor"):
        return "SOURCE_RECHECK_REQUIRED"
    if now >= recheck_at(result):
        return "SOURCE_RECHECK_REQUIRED"
    return "BRIEF_REVIEW_REQUIRED" if result["status"] == "DRAFT" else None


def research_package(brief, brand_name, load, clock, previous=None):
    sources = [capture(source, load, clock) for source in (brief.source, brief.peer_source)]
    if previous is None:
        return proposal(brief, brand_name, sources)
    changes = [
        {"field": before["page_url"], "before": before["outcome"], "after": after["outcome"],
         "before_sha256": before.get("text_sha256"), "after_sha256": after.get("text_sha256")}
        for before, after in zip(previous["evidence"], sources)
        if (before["outcome"], before.get("text_sha256")) != (after["outcome"], after.get("text_sha256"))
    ]
    if not changes and not check(previous, clock()):
        return None
    return proposal(brief, brand_name, sources, changes=changes)
