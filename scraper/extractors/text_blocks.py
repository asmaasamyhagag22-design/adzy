"""Text-block extraction.

For each meaningful text-bearing element, produce a TextBlock that
captures the text, its tag, its section (header/nav/main/footer/aside),
a CSS selector pointing to it, its bounding box (so we can decide
above-fold), and href if it's a link.

We run a single page.evaluate() inside the browser to gather all of
this in one shot — building selectors and bounding boxes in JS is
faster and more accurate than re-parsing HTML in Python.

Output: a list[TextBlock] with stable-within-scrape block_ids.
"""
from __future__ import annotations

from typing import Optional

from playwright.sync_api import Page

from ..schemas import BoundingBox, Section, TextBlock


# The JS that runs in the page context. Returns a list of raw dicts.
_DOM_WALKER_JS = r"""
() => {
    // Tags whose text we treat as a "block"
    const KEEP_TAGS = new Set([
        'H1','H2','H3','H4','H5','H6',
        'P','LI','BUTTON','A','SPAN','LABEL','DIV',
        'STRONG','EM','BLOCKQUOTE','SUMMARY','FIGCAPTION','CAPTION','TD','TH'
    ]);

    // Tags we never enter
    const SKIP_TAGS = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','SVG','IFRAME']);

    const out = [];
    const vh = window.innerHeight || 800;
    const vw = window.innerWidth || 1366;
    const viewportTopAbs = window.scrollY || 0;

    function buildSelector(el) {
        // A short, robust-enough selector. Prefer id; otherwise build
        // a path of tag[:nth-of-type] from the closest ancestor with an id.
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + CSS.escape(el.id);

        const parts = [];
        let node = el;

        while (node && node.nodeType === 1 && node.tagName !== 'HTML') {
            let part = node.tagName.toLowerCase();
            const parent = node.parentElement;

            if (parent) {
                const sameTag = Array.from(parent.children).filter(
                    c => c.tagName === node.tagName
                );

                if (sameTag.length > 1) {
                    const idx = sameTag.indexOf(node) + 1;
                    part += ':nth-of-type(' + idx + ')';
                }
            }

            parts.unshift(part);

            if (node.id) {
                parts[0] = part + '#' + CSS.escape(node.id);
                break;
            }

            node = parent;

            if (parts.length > 8) break; // cap depth
        }

        return parts.join(' > ');
    }

    function sectionFor(el) {
        // Walk up looking for a semantic ancestor.
        let n = el;

        while (n && n.nodeType === 1 && n.tagName !== 'BODY') {
            const t = n.tagName;

            if (t === 'HEADER') return 'header';
            if (t === 'NAV') return 'nav';
            if (t === 'FOOTER') return 'footer';
            if (t === 'ASIDE') return 'aside';
            if (t === 'MAIN' || t === 'ARTICLE' || t === 'SECTION') return 'main';

            // role attributes
            const role = n.getAttribute && n.getAttribute('role');

            if (role) {
                if (role === 'banner') return 'header';
                if (role === 'navigation') return 'nav';
                if (role === 'contentinfo') return 'footer';
                if (role === 'main') return 'main';
                if (role === 'complementary') return 'aside';
            }

            n = n.parentElement;
        }

        return 'unknown';
    }

    function visible(el) {
        const rect = el.getBoundingClientRect();

        if (rect.width === 0 || rect.height === 0) return false;

        const style = window.getComputedStyle(el);

        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity || '1') === 0) return false;

        return true;
    }

    function offscreen(rect) {
        // Remove elements that are outside or partially pushed outside
        // the horizontal viewport. This catches hidden social sidebars
        // like x = -158.
        if (rect.right <= 0) return true;
        if (rect.left < 0) return true;
        if (rect.left >= vw) return true;
        return false;
    }

    function normalizeHref(href) {
        if (!href) return null;

        try {
            return new URL(href, document.baseURI).href;
        } catch (e) {
            return href;
        }
    }

    function linkInfo(el) {
        // A text block can be:
        // 1. an <a> itself
        // 2. a child inside an <a>, e.g. <a><span>Facebook</span></a>
        // 3. a parent containing an <a>, e.g. <li><a>Facebook</a></li>
        //
        // We capture href in all three cases so evidence blocks can be linked.
        if (!el || el.nodeType !== 1) {
            return {is_link: false, href: null};
        }

        if (el.tagName === 'A') {
            return {
                is_link: true,
                href: normalizeHref(el.getAttribute('href') || null)
            };
        }

        const closestAnchor = el.closest && el.closest('a[href]');
        if (closestAnchor) {
            return {
                is_link: true,
                href: normalizeHref(closestAnchor.getAttribute('href') || null)
            };
        }

        const childAnchor = el.querySelector && el.querySelector('a[href]');
        if (childAnchor) {
            return {
                is_link: true,
                href: normalizeHref(childAnchor.getAttribute('href') || null)
            };
        }

        return {is_link: false, href: null};
    }

    const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_ELEMENT,
        {
            acceptNode: (n) => {
                if (SKIP_TAGS.has(n.tagName)) return NodeFilter.FILTER_REJECT;
                return NodeFilter.FILTER_ACCEPT;
            }
        }
    );

    let counter = 0;

    while (walker.nextNode()) {
        const el = walker.currentNode;

        if (!KEEP_TAGS.has(el.tagName)) continue;
        if (!visible(el)) continue;

        const rect = el.getBoundingClientRect();

        if (offscreen(rect)) continue;

        const text = (el.innerText || '').trim();

        if (!text) continue;
        if (text.length < 2) continue;
        if (text.length > 1000) continue; // skip giant containers

        // De-duplicate: if my text equals my parent's just-recorded text,
        // skip me. This avoids capturing a <span> inside an <a> twice.
        //
        // ...but the TAG is meaning, and the walker runs in document order, so the WRAPPER is
        // always recorded first and the semantic child is always the one dropped. Measured on
        // an Al Fouad product page: the page has a visible <h1> carrying the product name, the
        // walker returned 174 blocks and ZERO h1 — the name survived only as `tag=div`, because
        // the theme wraps it as <div><h1>name</h1></div>. Downstream, `catalog._name_from_page`
        // reads h1/h2/h3/title only, so it never saw the product name and fell through to a
        // "Frequently Bought Together" carousel heading: 24 catalog items named after the WRONG
        // product. The evidence was collected, serialized, and unreadable.
        //
        // A wrapper whose text is EXACTLY its child's text adds nothing but nesting. When the
        // child is a heading and the recorded parent is not, UPGRADE the record in place rather
        // than dropping the child: same block count, same text, the tag that carries meaning.
        // One-directional on purpose — <h1><span>x</span></h1> still keeps the h1.
        if (out.length > 0) {
            const last = out[out.length - 1];
            if (last.text === text) {
                const isHeading = /^H[1-6]$/.test(el.tagName);
                const wasHeading = /^h[1-6]$/.test(last.tag);
                if (!isHeading || wasHeading) continue;
                last.tag = el.tagName.toLowerCase();
                last.selector = buildSelector(el);
                last.section = sectionFor(el);
                continue;
            }
        }

        const absY = rect.top + viewportTopAbs;
        const aboveFold = absY < vh;

        const tag = el.tagName.toLowerCase();
        const li = linkInfo(el);

        out.push({
            index: counter++,
            tag: tag,
            text: text,
            section: sectionFor(el),
            selector: buildSelector(el),
            bbox: {
                x: rect.left + (window.scrollX || 0),
                y: absY,
                width: rect.width,
                height: rect.height
            },
            above_fold: aboveFold,
            is_link: li.is_link,
            href: li.href
        });

        if (out.length > 800) break; // hard cap per page
    }

    return out;
}
"""


def _slug_for_url(url: str) -> str:
    """Turn a URL into a short slug for block_id prefixing."""
    from urllib.parse import urlparse

    p = urlparse(url)
    path = (p.path or "/").strip("/").replace("/", "_")
    return (path or "home").lower()[:40]


def extract_text_blocks(page: Page, page_url: str) -> list[TextBlock]:
    """Run the DOM walker on the given live Page and convert to TextBlocks."""
    try:
        raw_blocks = page.evaluate(_DOM_WALKER_JS) or []
    except Exception:
        return []

    slug = _slug_for_url(page_url)
    blocks: list[TextBlock] = []

    for raw in raw_blocks:
        try:
            section_str = raw.get("section", "unknown")

            try:
                section = Section(section_str)
            except ValueError:
                section = Section.UNKNOWN

            bbox_raw = raw.get("bbox") or {}
            bbox: Optional[BoundingBox] = None

            if bbox_raw:
                try:
                    bbox = BoundingBox(
                        x=float(bbox_raw.get("x", 0)),
                        y=float(bbox_raw.get("y", 0)),
                        width=float(bbox_raw.get("width", 0)),
                        height=float(bbox_raw.get("height", 0)),
                    )
                except (TypeError, ValueError):
                    bbox = None

            idx = raw.get("index", len(blocks))
            tag = str(raw.get("tag", "div"))
            block_id = f"{slug}_{tag}_{idx:04d}"

            blocks.append(
                TextBlock(
                    block_id=block_id,
                    page_url=page_url,
                    tag=tag,
                    text=str(raw.get("text", ""))[:1000],
                    section=section,
                    selector=str(raw.get("selector", "")),
                    bbox=bbox,
                    above_fold=bool(raw.get("above_fold", False)),
                    is_link=bool(raw.get("is_link", False)),
                    href=raw.get("href"),
                )
            )

        except Exception:
            # Skip malformed blocks rather than fail the whole page.
            continue

    return blocks