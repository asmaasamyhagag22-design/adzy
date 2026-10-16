"""Contact channels: passthrough from manifest.contact.

Every record in the manifest already has an evidence_block_id (or
None for href-derived). We promote those into EvidenceItems on the
ContactChannels object.

2026-05 PR-text-contact-scan: also scans manifest text_blocks for
phone numbers and emails written as plain text (not as tel:/mailto:
hrefs). This catches contact info that the scraper's href-only
extraction misses — e.g. Glamira's "(888) 631-7760" in the page
body, or Elkbabgi's "15959" hotline shown as a number next to
"Contact Us" in the footer.

Universal behavior: works on every site whose manifest has
text_blocks containing phone-shaped or email-shaped strings.
Whether the phone is in a footer, an about page, or a contact form
header — if it's in the captured text, we extract it.
"""
from __future__ import annotations

import re
from typing import Optional

import phonenumbers

from scraper.schemas import ScrapeManifest
from scraper.url_utils import region_from_site

from ..schemas import ContactChannels, EvidenceItem, PhoneChannel


# Strict email regex: at least one alphanumeric before the @, allows
# dots/dashes/underscores in local part, requires a TLD of 2+ letters.
# Conservative on purpose — false positives in this layer become wrong
# contact info in the profile.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Short-code / hotline scanner: 5-digit hotlines that appear adjacent
# to a trigger word in the surrounding text.
#
# 2026-06 (PR-D / "phone pollution fix"): tightened after the benchmark
# revealed widespread false positives — postal codes, P.O. boxes, tax
# numbers, menu prices, copyright years were all leaking through.
#
# Real Egyptian hotlines in the test grid are all exactly 5 digits:
#   19914 (Buffalo Burger), 16723 (AUC SCE), 19885 (As-Salam),
#   16781 (Andalusia), 15959 (Elkbabgi).
# 3-4 digit standalone codes are almost always fragments of other
# numbers (prices, P.O. box numbers, tax IDs). 6-7 digit codes are
# rarely hotlines and usually addresses or IDs.
#
# Conservatism cost: a site with a 4-digit or 6-digit hotline would be
# missed. That risk is accepted; the false-positive rate at min=3 was
# catastrophic (12 of 14 URLs polluted in the 2026-06-01 run).
_SHORT_CODE_TRIGGER_WORDS = {
    # English
    "hotline", "call", "call us", "phone", "tel", "telephone",
    "contact", "dial", "talk to us",
    # Arabic
    "اتصل", "اتصلوا", "الخط", "هاتف", "الاتصال", "ساخن",
    "تليفون", "حجز موعد", "رقم التليفون",
}
_SHORT_CODE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")

# Currency tokens that, when preceding a digit sequence within ~10 chars,
# mark it as a price rather than a hotline. Includes Egyptian Pound
# variants and major others.
_CURRENCY_RE = re.compile(
    r"(?:EGP|USD|GBP|EUR|AED|SAR|KWD|\$|£|€|ج\.م|جنيه)\s*$",
    re.IGNORECASE,
)

# Proximity logic: rather than a flat character window (which breaks on
# Arabic where trigger words are 6+ chars themselves), we require the
# trigger word and digit run to be in the same "clause" — a chunk of
# text bounded by sentence/clause-level punctuation. Real hotlines sit
# in tight phrases like "Call us 19914" or "الخط الساخن 16723";
# postal codes sit across clause boundaries like "Cairo 11835, Egypt
# Hotline 16723" or "11835، مصر الخط الساخن 16723".
_CLAUSE_SEPARATOR_RE = re.compile(r"[.,،;•·\n\r]+")


def _scan_text_for_phones(text: str, region: Optional[str] = None) -> list[str]:
    """Return E.164-formatted phone numbers found in `text`.

    Uses phonenumbers.PhoneNumberMatcher with leniency=VALID — validates
    against real number formats. Rejects "© 2018-2024", tracking IDs,
    build hashes, timestamps.

    Local-format numbers are interpreted only when ``region`` came from
    genuine site context. With ``region=None``, only numbers carrying their
    own international prefix can be normalized; unknown does not become EG
    (or any other market) merely because the digits fit that market.
    """
    out: list[str] = []
    if not text or len(text) > 50_000:
        return out
    seen = set()
    try:
        for match in phonenumbers.PhoneNumberMatcher(
            text, region,
            leniency=phonenumbers.Leniency.VALID,
        ):
            e164 = phonenumbers.format_number(
                match.number, phonenumbers.PhoneNumberFormat.E164,
            )
            if e164 and e164 not in seen:
                seen.add(e164)
                out.append(e164)
    except Exception:
        # Never crash profile extraction over one malformed text block.
        pass
    return out


def _phone_region_for_manifest(manifest: ScrapeManifest) -> Optional[str]:
    """Resolve phone context only from URLs already captured in the manifest."""
    meta = manifest.scrape_meta
    candidates = [meta.final_url, meta.normalized_url, meta.input_url]
    for page in manifest.pages:
        candidates.extend([page.final_url, page.url])
    for url in candidates:
        region = region_from_site(url)
        if region:
            return region
    return None


def _scan_text_for_short_codes(text: str) -> list[str]:
    """Return short-code / hotline digit-strings (exactly 5 digits) found
    in `text`. A digit run is accepted iff:
      1. Within the same clause (punctuation-bounded chunk), at least
         one trigger word appears.
      2. Not preceded by a currency token (`EGP 159` is a price).
      3. Not part of a hyphenated number group (`298-758-970` is a tax ID).

    Examples:
      "Call us 19914"                      -> ["19914"]
      "Hotline 16723"                      -> ["16723"]
      "Cairo 11835, Egypt Hotline 16723"   -> ["16723"]   (clauses split)
      "11835، مصر الخط الساخن 16723"       -> ["16723"]   (Arabic clauses)
      "Tax No: 298-758-970"                -> []          (no 5-digit run)
      "EGP 159.00 Submit Call Us 19914"    -> ["19914"]   (clauses split)
    """
    if not text:
        return []
    # Cheap early exit
    lower = text.lower()
    if not any(tw in lower for tw in _SHORT_CODE_TRIGGER_WORDS):
        return []

    out: list[str] = []
    seen: set[str] = set()

    # Split the text into clauses on punctuation. Track each clause's
    # start offset so we can do absolute hyphen/currency checks correctly.
    pos = 0
    while pos <= len(text):
        # Find next separator (or end of text)
        sep_match = _CLAUSE_SEPARATOR_RE.search(text, pos)
        clause_end = sep_match.start() if sep_match else len(text)
        clause = text[pos:clause_end]
        clause_lower = clause.lower()

        # Does this clause contain a trigger word?
        if any(tw in clause_lower for tw in _SHORT_CODE_TRIGGER_WORDS):
            # Find 5-digit runs within this clause
            for match in _SHORT_CODE_RE.finditer(clause):
                digits = match.group(1)
                if digits in seen:
                    continue

                # Absolute position in `text` for hyphen / currency guards
                abs_start = pos + match.start(1)
                abs_end = pos + match.end(1)

                # Hyphen guard: digit run is part of hyphenated group
                prev_ch = text[abs_start - 1] if abs_start > 0 else ""
                next_ch = text[abs_end] if abs_end < len(text) else ""
                if prev_ch == "-" or next_ch == "-":
                    continue

                # Currency guard: preceded by EGP/USD/etc within 10 chars
                before_slice = text[max(0, abs_start - 10):abs_start]
                if _CURRENCY_RE.search(before_slice):
                    continue

                seen.add(digits)
                out.append(digits)

        # Advance past separator
        if sep_match:
            pos = sep_match.end()
        else:
            break

    return out


def _scan_text_for_emails(text: str) -> list[str]:
    """Return unique emails found in `text`."""
    if not text or len(text) > 50_000:
        return []
    seen: list[str] = []
    for match in _EMAIL_RE.finditer(text):
        addr = match.group(0).lower()
        # Filter obvious noise: emails ending in .jpg/.png/.pdf, single-char TLDs
        if any(addr.endswith(f".{ext}") for ext in
               ("jpg", "jpeg", "png", "gif", "webp", "svg", "pdf",
                "css", "js", "json", "html", "xml")):
            continue
        if addr not in seen:
            seen.append(addr)
    return seen


def extract_contact(manifest: ScrapeManifest) -> ContactChannels:
    c = manifest.contact

    phones: list = []  # list[PhoneChannel]
    seen_phone_keys: set[str] = set()
    evidence: list[EvidenceItem] = []

    for p in c.phones:
        # 2026-05: now handles both E.164 numbers and short-code hotlines.
        # Dedupe key: prefer e164 when present, else raw (short-code digits).
        key = p.e164 or p.raw
        if not key or key in seen_phone_keys:
            continue
        seen_phone_keys.add(key)
        phones.append(PhoneChannel(
            e164=p.e164,
            raw=p.raw,
            is_short_code=p.is_short_code,
        ))
        evidence.append(EvidenceItem(
            block_id=p.evidence_block_id,
            page_url=p.page_url,
            quote=p.raw[:120] if p.raw else None,
            extractor=(
                "rule:phone_hotline" if p.is_short_code
                else "rule:phone" if p.evidence_block_id
                else "rule:tel_link"
            ),
        ))

    emails: list[str] = []
    seen_emails: set[str] = set()
    for e in c.emails:
        key = e.address.lower()
        if key not in seen_emails:
            seen_emails.add(key)
            emails.append(e.address)
            evidence.append(EvidenceItem(
                block_id=e.evidence_block_id,
                page_url=e.page_url,
                quote=e.address,
                extractor="rule:email" if e.evidence_block_id else "rule:mailto_link",
            ))

    whatsapp: list[str] = []
    seen_wa: set[str] = set()
    for w in c.whatsapp:
        key = w.number or w.raw_url
        if key and key not in seen_wa:
            seen_wa.add(key)
            if w.number:
                # Normalize to +E.164 form when only digits given
                num = w.number if w.number.startswith("+") else "+" + w.number
                whatsapp.append(num)
            evidence.append(EvidenceItem(
                block_id=None,
                page_url=w.page_url,
                quote=w.raw_url,
                extractor="rule:wa_link",
            ))

    physical_addresses: list[str] = []
    seen_addrs: set[str] = set()
    for a in c.addresses_raw:
        if a.text and a.text not in seen_addrs:
            seen_addrs.add(a.text)
            physical_addresses.append(a.text)
            evidence.append(EvidenceItem(
                block_id=a.evidence_block_id,
                page_url=a.page_url,
                quote=a.text[:200],
                extractor="rule:schema_org_address" if not a.evidence_block_id
                          else "rule:address_in_text",
            ))

    map_embed_present = len(c.map_embeds) > 0
    if map_embed_present:
        evidence.append(EvidenceItem(
            block_id=None,
            page_url=c.map_embeds[0].page_url,
            quote=c.map_embeds[0].src[:200],
            extractor=f"rule:map_iframe:{c.map_embeds[0].provider}",
        ))

    # has_contact_form: any form on any page
    has_contact_form = any(len(p.forms) > 0 for p in manifest.pages)
    if has_contact_form:
        for p in manifest.pages:
            if p.forms:
                evidence.append(EvidenceItem(
                    block_id=None,
                    page_url=p.final_url,
                    quote=p.forms[0].surrounding_text or "<form>",
                    extractor="rule:form_present",
                ))
                break

    # 2026-05 PR-text-contact-scan: text-block fallback pass.
    # Scan every TextBlock in the manifest for phone numbers and emails
    # written as plain text. These would have been missed by the scraper's
    # href-only extractor (which reads <a href="tel:..."> and <a href="mailto:...">)
    # but are commonly present in page footers, headers, and contact-page
    # bodies as plain text.
    #
    # Uses phonenumbers.PhoneNumberMatcher with leniency=VALID so
    # tracking IDs, timestamps, and "© 2018-2024" don't slip through.
    # Short-codes (3-7 digit hotlines) require a trigger word in the
    # surrounding text — same conservative gate.
    phone_region = _phone_region_for_manifest(manifest)
    for page in manifest.pages:
        for block in page.text_blocks:
            block_text = block.text or ""
            if not block_text:
                continue

            # Full international phones
            for e164 in _scan_text_for_phones(block_text, region=phone_region):
                if e164 in seen_phone_keys:
                    continue
                seen_phone_keys.add(e164)
                phones.append(PhoneChannel(
                    e164=e164, raw=e164, is_short_code=False,
                ))
                evidence.append(EvidenceItem(
                    block_id=block.block_id,
                    page_url=block.page_url,
                    quote=block_text[:120],
                    extractor="rule:text_scan_phone",
                ))

            # Short codes / hotlines
            for digits in _scan_text_for_short_codes(block_text):
                if digits in seen_phone_keys:
                    continue
                seen_phone_keys.add(digits)
                phones.append(PhoneChannel(
                    e164=None, raw=digits, is_short_code=True,
                ))
                evidence.append(EvidenceItem(
                    block_id=block.block_id,
                    page_url=block.page_url,
                    quote=block_text[:120],
                    extractor="rule:text_scan_hotline",
                ))

            # Emails
            for addr in _scan_text_for_emails(block_text):
                if addr in seen_emails:
                    continue
                seen_emails.add(addr)
                emails.append(addr)
                evidence.append(EvidenceItem(
                    block_id=block.block_id,
                    page_url=block.page_url,
                    quote=addr,
                    extractor="rule:text_scan_email",
                ))

    # 2026-06 PR-D / D3: substring dedup against full phones.
    # If a full E.164 phone (e.g. "+18886317760") was extracted AND a
    # short code is a digit-substring of it (e.g. "888", "631", "7760"),
    # the short code is a fragment, not a real hotline. Drop it.
    full_phone_digits = [
        p.e164.lstrip("+") for p in phones
        if p.e164 and not p.is_short_code
    ]
    if full_phone_digits:
        kept_phones = []
        for p in phones:
            if p.is_short_code and p.raw:
                if any(p.raw in full for full in full_phone_digits):
                    # Skip — this short code is a fragment of a full phone
                    continue
            kept_phones.append(p)
        phones = kept_phones

    return ContactChannels(
        phones=phones,
        emails=emails,
        whatsapp_numbers=whatsapp,
        has_contact_form=has_contact_form,
        physical_addresses=physical_addresses,
        map_embed_present=map_embed_present,
        evidence=evidence,
    )
