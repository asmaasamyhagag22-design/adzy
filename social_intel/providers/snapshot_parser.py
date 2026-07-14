"""Tier-3 snapshot provider — parses HUMAN-SAVED HTML snapshots of a page's posts/comments.

The agent NEVER logs into or automates FB/IG (PD-10): saving a snapshot is a human act; this only
PARSES the saved file (offline, no network). Author names are hashed at ingestion (PD-6). Stdlib
`html.parser` only — no new dependency.

Snapshot format (documented, human-fillable; the real FB/IG selectors are pending owner raw samples,
A5): any element carrying a `data-author` attribute is one signal, with optional `data-kind`
(comment|post|reply|caption), `data-lang`, `data-likes`, `data-url`, `data-own` (own-brand?) and the
element's text as the signal text. Empty list is VALID (advisor flag).
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional

from ..schemas import SocialSignal, hash_author

_KINDS = {"comment", "post", "reply", "caption"}


class _SnapshotHTMLParser(HTMLParser):
    """Collect (attrs, text) for every element that carries a data-author attribute."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records: List[tuple] = []
        self._frame: Optional[list] = None   # [attrs, depth, text_parts] while capturing one element

    def handle_starttag(self, tag, attrs):
        ad = {k: (v or "") for k, v in attrs}
        if self._frame is not None:
            self._frame[1] += 1                      # a nested open inside the captured element
        elif "data-author" in ad:
            self._frame = [ad, 0, []]

    def handle_startendtag(self, tag, attrs):        # self-closing <br/> etc. — no depth change
        pass

    def handle_endtag(self, tag):
        if self._frame is None:
            return
        if self._frame[1] > 0:
            self._frame[1] -= 1
        else:
            self.records.append((self._frame[0], "".join(self._frame[2]).strip()))
            self._frame = None

    def handle_data(self, data):
        if self._frame is not None:
            self._frame[2].append(data)


def _to_int(v: str) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


class SnapshotSocialProvider:
    source = "snapshot"

    def __init__(self, snapshots_dir: Optional[str] = None):
        base = Path(__file__).resolve().parent.parent / "fixtures" / "snapshots"
        self.dir = Path(snapshots_dir) if snapshots_dir else base

    def fetch(self, brand_ref: str, *, limit: int = 200) -> List[SocialSignal]:
        path = None
        for ext in (".html", ".htm"):
            cand = self.dir / f"{brand_ref}{ext}"
            if cand.is_file():
                path = cand
                break
        if path is None:
            return []
        parser = _SnapshotHTMLParser()
        try:
            parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            return []
        out: List[SocialSignal] = []
        for attrs, text in parser.records[:limit]:
            if not text:
                continue
            kind = (attrs.get("data-kind") or "comment").strip().lower()
            if kind not in _KINDS:
                kind = "comment"
            try:
                out.append(SocialSignal(
                    source="snapshot",
                    source_url=(attrs.get("data-url") or f"snapshot://{brand_ref}").strip(),
                    signal_kind=kind,
                    author_hash=hash_author(attrs.get("data-author") or "anonymous"),
                    text=text, lang=(attrs.get("data-lang") or "").strip(),
                    like_count=_to_int(attrs.get("data-likes")),
                    brand_ref=brand_ref,
                    is_own_brand=str(attrs.get("data-own") or "").strip().lower() in ("1", "true", "yes"),
                    scraped_at=datetime.now(timezone.utc)))
            except Exception:  # noqa: BLE001
                continue
        return out
