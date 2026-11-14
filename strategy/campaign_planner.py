"""Bounded public research and conservative compilation for campaign briefs."""
from collections import Counter
from hashlib import sha256
import re
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urldefrag, urlsplit
from time import monotonic

from pydantic import Field, ValidationError

from scraper.extractors.links import extract_links_from_html
from strategy.campaign_package import Brief, Input, Source, digest, proposal, public_soup, valid_source_url
from strategy.campaign_research import ResearchGap, same_source
from strategy.campaign_evidence import (
    CONFLICT_SIGNAL, EvidenceAnchor, INSTRUCTION, QUALIFIER_LABEL, REFERENCE, SourceBlock, anchor_for,
)
from strategy.campaign_v2 import CampaignConflict
from strategy.campaign_editions import build_edition, edit_edition_copy, find_edition
from strategy.campaign_copy import CopyEdit, plan_copy, source_read_cta, validate_copy, writing_for
from strategy.campaign_release import (
    GUARDRAILS, HEADING_REVIEW_POLICY, SOURCE_CTA_POLICY, STABLE_GUARDRAILS, active_release, release_metadata,
)
from workflows.campaign_telemetry import CampaignTrace, source_error


PAGE_LIMIT = 4
SECONDS = 12.0
POLICY = "source-campaign-planner@3"
STOP = set("""increase grow drive boost get for from about the and into with people visit visits
explanation clicks click catalog page more a an to of in find want interested concerned
using use explain how what who discover build builds download downloads install
book bookings purchase purchases orders buy sale prices price boost awareness
زيادة زيارات زيارة رفع عن إلى في على من جذب صفحة مزيد المستخدمين عملاء جمهور شرح
تحميل تنزيل تثبيت شراء حجز أسعار سعر خصم عروض""".split())
PATTERNS = {
    "DOWNLOAD": r"\b(downloads?|install(?:s|ation)?)\b|تحميل|تنزيل|تثبيت",
    "OFFER": r"\b(discounts?|offers?|prices?|sales?|promo(?:tion)?)\b|سعر|أسعار|خصم|عرض",
    "TRANSACTION": r"\b(bookings?|reserve|reservations?|buy|purchases?|orders?|tickets?|sign.?ups?|donations?)\b|حجز|شراء|طلب|تذاكر|تبرع|اشتراك",
}
UNSAFE_URL = re.compile(r"login|logout|cart|checkout|register|signup|unsubscribe|account|(?:^|/)api(?:/|$)", re.I)
BLOCK_ELEMENTS = {"p", "li", "dt", "dd", "tr", "blockquote"}
NAMED_ENTITY = re.compile(r"\b[A-Z][\w-]*(?:[ \t]+[A-Z][\w-]*)+\b")
SENTENCE = re.compile(r"\S.*?(?:[.!?؟](?=\s|$)|$)")


class PlanInput(Input):
    url: str = Field(min_length=10, max_length=500)
    objective: str = Field(min_length=8, max_length=200)
    language: str = Field(pattern=r"^(ar|en)(?:-[A-Za-z0-9]{2,8}){0,2}$")


class BriefReview(Input):
    audience: str = Field(min_length=4, max_length=200)
    tone: str = Field(min_length=2, max_length=75)
    selected: int = Field(ge=0, le=2)
    copy_edit: CopyEdit | None = None


def words(text):
    tokens = set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())) - STOP
    return {re.sub(r"(?:ers?|ing|s)$", "", word) if re.fullmatch(r"[a-z]{4,}(?:ers?|ing|s)", word)
            else word for word in tokens}


def clean(text):
    return " ".join(text.split())


def goal_kind(objective):
    return next((kind for kind, pattern in PATTERNS.items() if re.search(pattern, objective, re.I)), "DISCOVERY")


def public_link(url, root):
    parts = urlsplit(url)
    return valid_source_url(url) and same_source(url, root) and not UNSAFE_URL.search(parts.path)


def phrase(text):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def mismatched_subject(text, heading_terms):
    return len(heading_terms) >= 2 and any(
        (entity_terms := words(entity)) & heading_terms and not heading_terms <= entity_terms
        for entity in NAMED_ENTITY.findall(text)
    )


def inspect(page, clock, terms, objective=""):
    soup = public_soup(page.body)
    body = soup.body
    if body is None:
        raise ResearchGap("SOURCE_TEXT_MISSING")
    text_hash = digest(clean(body.get_text(" ", strip=True)))
    heading = ""
    heading_parent = None
    heading_number = 0
    qualifier_section = False
    entries = []
    named = set()
    headings = set()
    groups = {}
    objective_phrase = f" {phrase(objective)} "
    title = clean(soup.title.get_text(" ", strip=True))[:180] if soup.title else ""
    selector = "h1,h2,h3,h4,p,li,dt,dd,tr,blockquote"
    for ordinal, node in enumerate((body.select_one("main") or body).select(selector)):
        if node.find_parent(["header", "nav", "footer"]):
            continue
        aside = node.find_parent("aside") is not None
        text = clean(node.get_text(" ", strip=True))
        scope = node.find_parent(["section", "article"])
        if node.name not in BLOCK_ELEMENTS:
            if aside:
                continue
            if heading and (scope is None or scope is heading_parent) and QUALIFIER_LABEL.match(text):
                qualifier_section = True
                continue
            qualifier_section = False
            heading = text
            heading_number = ordinal
            heading_parent = scope or node.parent
            label = phrase(heading)
            if node.name != "h1" and 3 <= len(heading) <= 75:
                headings.add(label)
            if (node.name != "h1" and 3 <= len(heading) <= 75
                    and len(words(heading)) >= 2 and f" {label} " in objective_phrase):
                named.add(heading)
            continue
        if not text or not 3 <= len(heading) <= 75 or node.find_parent(BLOCK_ELEMENTS):
            continue
        if heading in named and ((scope is not None and scope is not heading_parent) or not any(
            parent is heading_parent for parent in node.parents
        )):
            continue
        groups.setdefault((heading_number, heading, id(scope)), []).append(
            SourceBlock(ordinal, node.name, text, aside or qualifier_section)
        )
    for (_, heading, _), group in groups.items():
        heading_terms = words(heading) & terms
        for index, block in enumerate(group):
            text = block.text
            if block.context_only or not 30 <= len(text) <= 1600 or INSTRUCTION.search(text):
                continue
            antecedent = group[index - 1].text if index and REFERENCE.match(text) else ""
            if mismatched_subject(text, heading_terms) or mismatched_subject(antecedent, heading_terms):
                continue
            for match in SENTENCE.finditer(text):
                sentence = match.group()
                if (not 30 <= len(sentence) <= 180 or sentence.endswith(":")
                        or re.match(r"^(?:just|simply)\b", sentence, re.I)):
                    continue
                score = len(words(sentence) & terms) + 2 * len(heading_terms)
                if score:
                    anchor = anchor_for(heading, group, index, match.start(), match.end(), words)
                    entries.append({"label": heading, "quote": sentence, "context": text, "score": score,
                                    "anchor": anchor.model_dump(mode="json")})
    for entry in entries:
        if entry["label"] in named:
            label = f" {phrase(entry['label'])} "
            context = f" {phrase(entry['context'])} "
            if any(label != f" {other} " and label in f" {other} " and f" {other} " in context
                   for other in headings):
                entry["mentions_other_variant"] = True
    # Retain short passages from every fetched source for the one-shot comparison.
    counts = Counter(word for entry in entries for word in words(entry["quote"]) & terms)
    for entry in entries:
        entry["specificity"] = round(sum(1 / counts[word] for word in words(entry["quote"]) & terms), 3)
    entries.sort(key=lambda entry: (-len(phrase(entry["label"]).split()) if entry["label"] in named else 0,
                                    -entry["specificity"], -entry["score"]))
    return {
        "page_url": page.url, "capture_kind": page.source_kind, "captured_at": page.captured_at or clock().isoformat(),
        "body_sha256": sha256(page.body).hexdigest(), "text_sha256": text_hash, "title": title,
        "entries": entries[:8],
        "named_sections": sorted(named),
    }


def rank_links(page, root, terms, visited, *, action=False):
    candidates = {}
    for link in extract_links_from_html(page.body.decode("utf-8", errors="replace"), page.url, root):
        url = urldefrag(link.href)[0]
        if len(url) > 500 or url in visited or not public_link(url, root):
            continue
        parts = urlsplit(url)
        anchor = link.anchor_text
        is_download = bool(re.search(r"\bdownload\b|تحميل|تنزيل", anchor, re.I))
        score = 3 * len(words(parts.path) & terms) + 2 * len(words(anchor) & terms)
        score -= 5 if parts.query else 0
        if action and is_download and re.search(r"/(?:download|downloads)(?:/|$)", parts.path, re.I):
            score += 20
        if score > candidates.get(url, (0, ""))[0]:
            candidates[url] = (score, anchor)
    return sorted(candidates.items(), key=lambda entry: (-entry[1][0], len(entry[0]), entry[0]))


def best_fact(pages, terms):
    named = {phrase(label) for page in pages for label in page.get("named_sections", [])}
    longest = max((len(label.split()) for label in named), default=0)
    named = {label for label in named if len(label.split()) == longest}
    candidates = [(page, item) for page in pages for item in page["entries"] if item["score"] >= 2]
    candidates = [(page, item) for page, item in candidates
                  if (not named or phrase(item["label"]) in named) and words(item["quote"]) & terms
                  and not item.get("mentions_other_variant")
                  and not CONFLICT_SIGNAL.search(item["quote"])]
    if not candidates:
        return None
    page, entry = max(candidates, key=lambda pair: (
        len(words(urlsplit(pair[0]["page_url"]).path) & terms),
        len(words(pair[1]["label"]) & terms),
        pair[1]["specificity"],
        len(words(pair[1]["quote"]) & words(pair[1]["label"]) & terms), pair[1]["score"],
        not bool(urlsplit(pair[0]["page_url"]).query), -len(pair[1]["quote"]),
    ))
    return {**{k: v for k, v in page.items() if k not in {"entries", "named_sections"}}, **entry,
            "scope": entry["label"], "outcome": "QUOTE_OBSERVED",
            "constraints": {"basis": entry["anchor"]["context_extent"],
                            "page_path": urlsplit(page["page_url"]).path,
                            "price": None, "branch": None, "valid_until": None,
                            "market": "UNCONFIRMED"}}


def conflicting_evidence(fact, pages):
    if not fact:
        return []
    subject = words(fact["label"])
    if not subject:
        return []
    nearby = [
        {"page_url": fact["page_url"], "captured_at": fact["captured_at"],
         "quote": span["quote"], "context": span["text"]}
        for span in fact["anchor"]["qualifications"] if CONFLICT_SIGNAL.search(span["quote"])
    ]
    if nearby:
        return nearby
    return [
        {"page_url": page["page_url"], "captured_at": page["captured_at"],
         "quote": entry["quote"], "context": entry["context"]}
        for page in pages for entry in page["entries"]
        if CONFLICT_SIGNAL.search(entry["quote"])
        and (subject <= words(entry["quote"])
             or (phrase(fact["label"]) == phrase(entry["label"])
                 and subject & words(entry["quote"])))
    ]


def question(kind, arabic):
    questions = {
        "DISCOVERY": ("أين يشرح الموقع الميزة المرتبطة بالهدف؟", "Which brand page describes the feature in the goal?"),
        "DOWNLOAD": ("أين شرح الميزة ورابط التنزيل المباشر المعتمد؟", "Where are the feature explanation and official download destination?"),
        "OFFER": ("أين الشروط المنشورة للسعر والمنطقة والصلاحية؟", "Where are the published price, market and validity terms?"),
        "TRANSACTION": ("أين رابط الإجراء وشروط الإتاحة للعميل؟", "Where is the action page with its eligibility and terms?"),
    }
    return questions[kind][not arabic]


def research_plan(request, load, clock, *, trace=None):
    started = monotonic()
    kind = goal_kind(request.objective)
    terms = words(request.objective)
    arabic = request.language.split("-")[0] == "ar"
    observed = trace if trace is not None else CampaignTrace()
    pages, steps, visited, links, action_links = [], [], set(), {}, {}
    url, reason, anchor = request.url, "BRAND_ENTRY", ""
    destination = None
    while url and len(visited) < PAGE_LIMIT and monotonic() - started < SECONDS:
        visited.add(url)
        step = {"page_url": url, "reason": reason, "anchor": anchor, "outcome": "SOURCE_UNAVAILABLE"}
        steps.append(step)
        try:
            page = load(url, min(5.0, max(.05, SECONDS - (monotonic() - started))))
            if page.url != url:
                raise ResearchGap("SOURCE_URL_CHANGED")
            captured = inspect(page, clock, terms, request.objective if kind == "DISCOVERY" else "")
            pages.append(captured)
            step["outcome"] = "SOURCE_READ"
            step["candidate_count"] = len(captured["entries"])
            for link, score in rank_links(page, request.url, terms, visited):
                if link not in links or links[link][0] < score[0]:
                    links[link] = score
            if kind == "DOWNLOAD":
                for link, score in rank_links(page, request.url, terms, visited, action=True):
                    if score[0] >= 20:
                        action_links[link] = score
                if reason == "ACTION_DESTINATION" and re.search(r"download|تحميل|تنزيل", captured["title"], re.I):
                    destination = {k: v for k, v in captured.items() if k not in {"entries", "named_sections"}}
                    destination.update(quote=captured["title"], context=captured["title"],
                                       scope="OFFICIAL_DESTINATION_TITLE", outcome="QUOTE_OBSERVED",
                                       anchor=anchor)
        except HTTPError as exc:
            step["outcome"] = "SOURCE_REDIRECT" if 300 <= exc.code < 400 else "SOURCE_UNAVAILABLE"
            step["http_status"] = exc.code
            exc.close()
        except (OSError, ResearchGap) as exc:
            step["outcome"] = source_error(exc)
        fact = best_fact(pages, terms)
        conflicts = conflicting_evidence(fact, pages)
        step["gap_after"] = "SOURCE_CONFLICT" if conflicts else "FEATURE_EVIDENCE" if fact is None else (
            "ACTION_DESTINATION" if kind == "DOWNLOAD" and not destination else
            "COMMERCIAL_TERMS" if kind in {"OFFER", "TRANSACTION"} else None)
        pending = {k: v for k, v in links.items() if k not in visited}
        actions = {k: v for k, v in action_links.items() if k not in visited}
        if conflicts:
            break
        if kind == "DOWNLOAD" and fact and not destination and actions:
            pending, reason = actions, "ACTION_DESTINATION"
        elif fact and kind in {"DISCOVERY", "DOWNLOAD"}:
            # A more specific discovered page may answer the same question directly.
            specificity = len(words(urlsplit(fact["page_url"]).path) & terms)
            pending = {k: v for k, v in pending.items() if len(words(urlsplit(k).path) & terms) > specificity and not urlsplit(k).query}
            reason = "MORE_SPECIFIC_FEATURE"
        else:
            reason = "FIND_COMMERCIAL_TERMS" if kind in {"OFFER", "TRANSACTION"} else "FILL_FEATURE_GAP"
        url = max(pending, key=lambda key: (pending[key][0], not bool(urlsplit(key).query), -len(key)), default=None)
        anchor = pending[url][1] if url else ""
    with observed.stage("campaign.select_evidence") as outcome:
        fact = best_fact(pages, terms)
        if fact is not None:
            outcome["evidence_hash"] = digest([fact["body_sha256"], fact["scope"], fact["quote"]])
        else:
            outcome.update(status="BLOCKED", reason="FEATURE_EVIDENCE")
    gap = steps[-1]["gap_after"] if steps else "FEATURE_EVIDENCE"
    if fact and kind == "DOWNLOAD" and gap != "SOURCE_CONFLICT":
        if fact["anchor"]["qualifications"]:
            gap = "QUALIFIED_ACTION"
        elif not destination:
            gap = "ACTION_DESTINATION"
    if kind in {"OFFER", "TRANSACTION"} and gap != "SOURCE_CONFLICT":
        gap = "COMMERCIAL_TERMS"
    missing = {
        "QUALIFIED_ACTION": ("راجع شروط الناشر قبل توجيه الجمهور للتنزيل.", "Review publisher conditions before promoting the download."),
        "SOURCE_CONFLICT": ("أي مصدر رسمي يوضح حالة الميزة الحالية؟", "Which official source confirms whether the feature is currently available?"),
        "FEATURE_EVIDENCE": ("أدخل رابط الصفحة الرسمية التي تشرح الميزة المطلوبة.", "Provide the official page explaining the feature in the goal."),
        "ACTION_DESTINATION": ("ما رابط صفحة التنزيل الرسمية التي تريد توجيه الجمهور إليها؟", "Which official download information page should the campaign link to?"),
        "COMMERCIAL_TERMS": ("ما الصفحة الرسمية التي تثبت شروط الإجراء والسعر إن وجد والنطاق والصلاحية؟", "Which official page confirms eligibility, price if relevant, market and validity?"),
    }
    plan = {
        "policy": POLICY, "input": request.model_dump(mode="json"), "intent": kind,
        "question": question(kind, arabic), "trace": steps, "pages": pages,
        "conflicts": conflicting_evidence(fact, pages),
        "gap": gap, "missing_question": missing[gap][not arabic] if gap else None,
        "stop": "SOURCE_CONFLICT" if gap == "SOURCE_CONFLICT" else "DECISION_EVIDENCE_FOUND" if not gap else
                "BUDGET_EXHAUSTED" if len(visited) >= PAGE_LIMIT or monotonic() - started >= SECONDS
                else "NO_MORE_RELEVANT_SOURCES",
        "page_calls": len(steps), "elapsed_ms": round((monotonic() - started) * 1000, 2),
        "provider_calls": 0, "cost_usd": 0, "brief_reviewed": False,
        "opportunity_hypothesis": (
            "اختبر هل ربط الميزة بالخطوة المطلوبة يرفع معدل النقر من الشريحة المقترحة."
            if arabic else "Test whether connecting the feature to a specific action improves clicks from the proposed audience."
        ),
    }
    return plan, fact, destination


def specific_heading(fact, objective):
    label = fact["label"]
    return len(label) <= 55 and len(words(label) & words(objective)) >= 2


def draft_brief(request, fact, destination, *, policy=STABLE_GUARDRAILS):
    arabic = request.language.split("-")[0] == "ar"
    language_index = not arabic
    label = fact["label"]
    proof = fact["quote"]
    qualified = bool(fact["anchor"]["qualifications"])
    topic_guide = ("راجع شروط الناشر للموضوع.", "Review the publisher’s conditions.")[language_index]
    guide = fact["page_url"]
    options = [
        {
            "action": "READ_FEATURE", "destination": guide, "evidence_index": 0,
            "name": ("شرح الميزة", "Explain the feature")[language_index],
            "audience": ("مهتمون يريدون فهم الميزة قبل اتخاذ قرار.", "People who want to understand the feature before acting.")[language_index],
            "message": ("ابدأ باسم الميزة ووجّه القارئ إلى شرحها الرسمي.", "Lead with the feature name and link to the publisher’s explanation.")[language_index],
            "headline": ("افهم الميزة أولًا", "Start with the feature")[language_index],
            "cta": ("اقرأ الشرح", "Read the explanation")[language_index],
            "metric": ("النقرات إلى الشرح لكل ظهور.", "Explanation clicks per impression.")[language_index],
            "tradeoff": ("يقلّل خطوة البحث للقارئ ويترك التقييم له.", "Shortens the path to the explanation and leaves the decision to the reader.")[language_index],
            "theme": "cobalt",
        },
        {
            "action": "COMPARE_FIT", "destination": guide, "evidence_index": 0,
            "name": ("قيّم الملاءمة", "Evaluate the fit")[language_index],
            "audience": ("أشخاص يقارنون بدائل ويحتاجون مراجعة التفاصيل.", "People comparing alternatives who need to inspect the details.")[language_index],
            "message": ("اطرح سؤال ملاءمة. استخدم شرح الناشر كنقطة للمقارنة.", "Ask a fit question. Use the publisher’s explanation as one input to a comparison.")[language_index],
            "headline": ("هل تناسب احتياجك؟", "Would it fit your needs?")[language_index],
            "cta": ("راجع تفاصيل الميزة", "Review the details")[language_index],
            "metric": ("زيارات صفحة الشرح بعد النقر على سؤال المقارنة.", "Visits to the explanation from the comparison creative.")[language_index],
            "tradeoff": ("يخدم مرحلة الاختيار ويحتاج بيانات قبل ادعاء تفوّق.", "Supports evaluation and would need comparative evidence for any superiority claim.")[language_index],
            "theme": "forest",
        },
        {
            "action": "DOWNLOAD_OPTIONS" if destination else "EXPLORE_TOPIC",
            "destination": destination["page_url"] if destination else guide,
            "evidence_index": 1 if destination else 0,
            "name": (("اختيار التنزيل", "Choose a download") if destination else ("استكشاف الموضوع", "Explore the topic"))[language_index],
            "audience": (("مهتمون وصلوا لمرحلة اختيار نسخة للتنزيل.", "People ready to choose a download option.") if destination else
                         ("جمهور في مرحلة الاستكشاف الأولي.", "People at the start of discovery."))[language_index],
            "message": (("وجّه المهتم إلى خيارات التنزيل الرسمية.", "Direct interested readers to official download options.") if destination else
                         ("قدّم اسم الموضوع كمدخل للقراءة المفتوحة.", "Use the topic name as an entry point for open-ended reading."))[language_index],
            "headline": (("اختر نسخة التنزيل", "Choose a download option") if destination else
                         ("من أين تبدأ؟", "Where would you start?"))[language_index],
            "cta": (("اعرض خيارات التنزيل", "View download options") if destination else
                    ("افتح صفحة الموضوع", "Explore the topic page"))[language_index],
            "metric": (("نقرات خيارات التنزيل، والتثبيت عند توفر رصد.", "Download-options clicks and installs when observable.") if destination else
                       ("النقرات من مادة الاستكشاف إلى صفحة الموضوع.", "Topic-page clicks from the discovery creative."))[language_index],
            "tradeoff": (("أقرب للتنزيل، ويحتاج رصد التثبيت لقياس النتيجة.", "Closer to download, with separate instrumentation needed for installation.") if destination else
                         ("أوسع في الاستكشاف ويستخدم نفس صفحة الشرح.", "Broader discovery with the same explanation page as the destination."))[language_index],
            "theme": "ember",
        },
    ]
    selected = 2 if destination else 0
    directions = []
    for option, writing in zip(options, plan_copy(fact, options, request.language)):
        title = option["headline"]
        if option["action"] == "READ_FEATURE" and specific_heading(fact, request.objective):
            title = label
        if qualified:
            card_message = topic_guide
        elif not arabic and len(proof) <= 105:
            card_message = proof
        else:
            card_message = label
        if title == card_message:
            card_message = publisher_guide(label, request.language)
        if (policy == SOURCE_CTA_POLICY and not qualified and destination is None and not writing and option["action"] == "READ_FEATURE"
                and option["destination"] == fact["page_url"] and title == label
                and specific_heading(fact, request.objective)):
            cta = source_read_cta(label, request.language)
            if cta:
                option["cta"] = cta
        caption = f"{title.rstrip('.?!؟')} — {card_message.rstrip('.?!؟')}. {option['cta']}."
        if arabic:
            caption = f"{title.rstrip('.?!؟')} · {card_message.rstrip('.?!؟')}. {option['cta']}."
        if writing:
            title, card_message, caption = writing.headline, writing.message, writing.caption
            option["cta"] = writing.cta
            option["concept"] = writing.concept
            if option["action"] == "COMPARE_FIT":
                title = ("إيه اللي يهمّك في الاختيار؟" if arabic else "What matters in the comparison?")
                caption = f"{title} {writing.caption}"
            elif option["action"] == "EXPLORE_TOPIC":
                title = ("ابدأ بسؤال" if arabic else "Start with a question")
                caption = f"{title} · {writing.caption}"
        directions.append({
            "name": option["name"], "rationale": option["message"], "headline": title,
            "message": card_message, "cta": option["cta"], "theme": option["theme"], "caption": caption,
            "hook": title, "shot": ("يظهر سؤال الحملة فوق شكل هندسي بسيط.", "Reveal the campaign prompt over a simple geometric motif.")[language_index],
            "middle_shot": ("يظهر اسم الميزة وتظل الخلفية ثابتة.", "Reveal the topic label and hold the background still.")[language_index],
            "end_shot": ("تظهر بطاقة باسم البراند والدعوة.", "End on the brand name and exact call to action.")[language_index],
        })
    if qualified:
        guide_headlines = [
            ("شروط الناشر", "Publisher conditions"),
            ("هل الشروط مناسبة لك؟", "Do the conditions fit your needs?"),
            ("ما الذي توضّحه الشروط؟", "What do the conditions cover?"),
        ]
        for option, direction, headlines in zip(options, directions, guide_headlines, strict=True):
            title = headlines[language_index]
            cta = ("راجع المصدر", "Check the source")[language_index]
            option["cta"] = cta
            direction.update(headline=title, hook=title, message=topic_guide, cta=cta,
                             caption=f"{title} {topic_guide} {cta}.")
    chosen = options[selected]
    assumption = ("الجمهور فرضية تحريرية. نختبر هل اهتمامه بالميزة يدفعه للنقر.",
                  "The audience is a working hypothesis. Test whether interest in the feature drives the intended click.")[language_index]
    reason = (("وجد البحث صفحة رسمية لخيارات التنزيل. لو تغيّرت الوجهة أو فقدنا شرح الميزة، أعد القرار.",
               "Research found an official download-options page. Reopen the choice if the destination or feature evidence changes.") if destination else
              ("الهدف زيارة شرح محدد. لو اختفى شرح الميزة أو تغيّر الهدف لإجراء، أعد البحث عن الوجهة.",
               "The goal is a visit to a specific explanation. Reopen the choice if it disappears or the goal changes to an action."))[language_index]
    brief = Brief.model_validate({
        "summary": topic_guide if qualified else fact["quote"], "audience": chosen["audience"],
        "market": ("نطاق السوق يحتاج تأكيدًا قبل النشر.", "Confirm market scope before publishing.")[language_index],
        "source": Source(url=guide, quote=fact["quote"], scope=fact["scope"]),
        "objective": request.objective, "metric": chosen["metric"], "assumption": assumption,
        "alternatives": [{"name": item["name"], "plan": item["message"], "tradeoff": item["tradeoff"]}
                         for item in options],
        "strategy_index": selected, "strategy_reason": reason,
        "tone": ("واضحة وهادئة", "Plain and concise")[language_index], "directions": directions,
        "selected": selected, "human_review": [
            ("راجع الاسم الأصلي للميزة واللغة ونطاق المصدر والحقوق.", "Review the original feature label, language, source scope and rights.")[language_index],
            ("راجع الوجهة على الموبايل ووسيلة قياس النتيجة.", "Check the destination on mobile and confirm the conversion measurement.")[language_index],
        ],
        "language": request.language, "direction": "rtl" if arabic else "ltr",
    })
    return brief, options


def build_plan(request, brand_name, load, clock, *, request_id=None, trace=None):
    trace = trace if trace is not None else CampaignTrace()
    release = active_release()
    snapshot_id = getattr(load, "campaign_snapshot_id", None)
    load = trace.loader(load)
    intent = goal_kind(request.objective)
    if intent == "DISCOVERY":
        if re.search(r"\b(?:menu|catalogue|catalog|range)\b|المنيو|القائمة|كتالوج", request.objective, re.I):
            intent = "BROWSE_CATALOG"
        elif re.search(r"\b(?:explore|discover|learn|ingredients)\b|تعرّف|التعرّف|مكونات|استكشاف", request.objective, re.I):
            intent = "INSPECT_ITEM"
    edition = find_edition(request, intent)
    if edition:
        return build_edition(request, edition, load, clock, request_id, trace=trace, release=release, snapshot_id=snapshot_id)
    with trace.stage("campaign.research") as outcome:
        planner, fact, destination = research_plan(request, load, clock, trace=trace)
        outcome.update(status="BLOCKED" if planner["gap"] else "OK", reason=planner["stop"])
    planner.update(request_id=request_id, trace_id=trace.ref, source_mode=trace.mode, snapshot_id=snapshot_id,
                   version=release_metadata(release, POLICY))
    if planner["gap"]:
        return proposal(None, brand_name, [fact] if fact else [], planner=planner)
    with trace.stage("campaign.write_copy") as outcome:
        brief, options = draft_brief(request, fact, destination, policy=GUARDRAILS[release])
        if options[0]["cta"] == source_read_cta(fact["label"], request.language):
            outcome["reason"] = "SOURCE_HEADING_CTA"
        sources = [fact] + ([destination] if destination else [])
        qualified = bool(fact["anchor"]["qualifications"])
        planner.update(options=options, qualified_source_guide=qualified, claims=[
            {"fields": [] if qualified else ["brief.summary", "package.copy.message"], "evidence_index": 0,
             "basis": "QUALIFIED_PUBLISHER_CONTEXT" if qualified else "PUBLISHER_QUOTE_AND_SECTION_LABEL",
             "semantic_review": "HUMAN_REVIEW_REQUIRED"},
        ])
        return proposal(brief, brand_name, sources, planner=planner)


def publisher_guide(label, language):
    if language.split("-")[0] == "ar":
        return f"اقرأ عن «{label}» لدى الناشر."
    return f"Read the publisher’s details on “{label}”."


def anchored_claim(previous, field, text):
    if field == "brief.summary":
        return text == previous["evidence"][0]["quote"]
    if field == "package.copy.message":
        evidence = previous["evidence"][0]
        return text in {evidence["quote"], evidence["label"],
                        publisher_guide(evidence["label"], previous["brief"]["language"])}
    return False


def review_draft(previous, edit, *, trace=None):
    trace = trace if trace is not None else CampaignTrace()
    with trace.stage("campaign.verify"):
        return _review_draft(previous, edit, trace.ref)


def _review_draft(previous, edit, trace_id):
    planner = {**previous["planner"], "brief_reviewed": True, "request_id": None}
    if trace_id:
        planner["trace_id"] = trace_id
    brief = Brief.model_validate(previous["brief"])
    if edit.selected >= len(brief.directions):
        raise CampaignConflict("DIRECTION_NOT_FOUND")
    option = planner["options"][edit.selected]
    if not planner.get("edition"):
        anchor = previous["evidence"][0].get("anchor")
        if anchor is None:
            raise CampaignConflict("SOURCE_RECHECK_REQUIRED")
        try:
            checked = EvidenceAnchor.model_validate(anchor)
        except ValidationError as exc:
            raise CampaignConflict("CREATIVE_BINDING_CHANGED") from exc
        if checked.subject != previous["evidence"][0]["scope"] or (
            checked.support.quote != previous["evidence"][0]["quote"]
        ) or (
            bool(checked.qualifications) != planner.get("qualified_source_guide", False)
        ):
            raise CampaignConflict("CREATIVE_BINDING_CHANGED")
    directions = list(brief.directions)
    if edit.copy_edit and planner.get("edition"):
        directions[edit.selected] = edit_edition_copy(directions[edit.selected], edit.copy_edit, option)
    elif edit.copy_edit:
        writing = writing_for(previous["evidence"][0], option["action"], brief.language)
        fact = previous["evidence"][0]
        source_heading = None
        guardrails = (planner.get("version") or {}).get("guardrails", {}).get("policy", STABLE_GUARDRAILS)
        # Only reuse the baseline's feature headline for directions pointing to the same source.
        if (guardrails in {HEADING_REVIEW_POLICY, SOURCE_CTA_POLICY} and not writing
                and not planner.get("qualified_source_guide")
                and option["destination"] == fact["page_url"]
                and specific_heading(fact, brief.objective)):
            source_heading = fact["label"]
        directions[edit.selected] = validate_copy(
            directions[edit.selected], edit.copy_edit, writing, source_heading=source_heading,
        )
    audience = edit.audience
    if planner.get("edition") and edit.selected != brief.selected:
        if audience == planner["options"][brief.selected]["audience"]:
            audience = option["audience"]
    revised = brief.model_copy(update={
        "audience": audience, "tone": edit.tone, "selected": edit.selected,
        "directions": tuple(directions),
        "strategy_index": edit.selected, "metric": option["metric"],
        "strategy_reason": brief.strategy_reason if brief.strategy_index == edit.selected else (
            "اختار المراجع هذا البديل ضمن نفس البحث." if brief.direction == "rtl"
            else "The reviewer selected this alternative using the same research."
        ),
    })
    changes = [{"field": key, "before": getattr(brief, key), "after": getattr(revised, key)}
               for key in ("audience", "tone", "selected") if getattr(brief, key) != getattr(revised, key)]
    if directions[edit.selected] != brief.directions[edit.selected]:
        before, after = brief.directions[edit.selected], directions[edit.selected]
        changes.extend({"field": field, "before": getattr(before, field), "after": getattr(after, field)}
                       for field in ("headline", "message", "cta", "caption")
                       if getattr(before, field) != getattr(after, field))
    result = proposal(revised, previous["brand_name"], previous["evidence"], changes=changes, planner=planner)
    if planner.get("edition") or planner.get("qualified_source_guide"):
        return result
    message = result["package"]["copy"]["message"]
    writing = writing_for(previous["evidence"][0], option["action"], brief.language)
    licensed = writing and writing.license("message", message)
    if not anchored_claim(result, "brief.summary", revised.summary) or (
        not anchored_claim(result, "package.copy.message", message) and not licensed
    ):
        raise CampaignConflict("CLAIM_EXCEEDS_SOURCE")
    return result


def refresh_plan(previous, load, clock, *, trace=None):
    request = PlanInput.model_validate(previous["planner"]["input"])
    updated = build_plan(request, previous["brand_name"], load, clock, trace=trace)
    old = {(item["page_url"], item["text_sha256"]) for item in previous["planner"]["pages"]}
    new = {(item["page_url"], item["text_sha256"]) for item in updated["planner"]["pages"]}
    from strategy.campaign_package import check

    unchanged = (
        old == new and previous["planner"].get("edition_sha256") == updated["planner"].get("edition_sha256")
        and previous["planner"]["gap"] == updated["planner"]["gap"]
        and [row.get("anchor") for row in previous["evidence"]] == [row.get("anchor") for row in updated["evidence"]]
        and [(row["page_url"], row["capture_kind"], row["quote"], row["scope"]) for row in previous["evidence"]]
        == [(row["page_url"], row["capture_kind"], row["quote"], row["scope"]) for row in updated["evidence"]]
    )
    if unchanged and check(previous, clock()) in (None, "BRIEF_REVIEW_REQUIRED"):
        return None
    updated["changes"] = [{"field": "research", "before": previous["planner"]["stop"],
                           "after": updated["planner"]["stop"], "changed_pages": sorted({url for url, _ in old ^ new})}]
    updated["sha256"] = digest({key: value for key, value in updated.items() if key != "sha256"})
    return updated
