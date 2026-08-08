"""Competitor Registry — the stable competitive baseline (owner-caught non-determinism).

THE FLAW (owner, 2026-07-12): every scrape rebuilds the competitor set from live search +
LLM selection, so "who are my competitors" drifts run-to-run and the SWOT quietly changes
underneath the client. THE FIX: the first approved discovery PERSISTS as the baseline (per
brand); every later run produces a DIFF against it — still-present ✓ / disappeared ⚠ / new
candidate → pending owner approval — never a silent replacement. SWOT computes against the
APPROVED registry, not the raw live pool. Identity changes (approve/remove) are the owner's
call (HITL), so this module only COMPUTES and PERSISTS; it never auto-approves a newcomer.

Pure + deterministic; JSON persistence under `registry/<brand_slug>.json` (gitignored — it is
per-machine run state, like runs/). Never raises for ordinary input.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

def _default_store() -> str:
    """The registry lives at the REPO ROOT, not wherever the process happens to be standing.

    `_STORE` used to be the bare string "registry", which `Path(store_dir)` resolves against the
    current working directory: the API started from one folder and a CLI run from another silently
    kept two different competitor baselines for the same brand — for a module whose entire promise
    is "the competitor set does not drift between runs". Same bug class as the webapp directory,
    which `tests/test_webapp_paths_are_anchored.py` was written for. `BASEERA_REGISTRY_DIR`
    overrides it for tests and for a machine that stores run state elsewhere."""
    import os
    override = os.environ.get("BASEERA_REGISTRY_DIR", "").strip()
    if override:
        return override
    return str(Path(__file__).resolve().parent.parent / "registry")


_STORE = _default_store()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def competitor_key(name: str = "", website: str = "") -> str:
    """A STABLE identity for a competitor — its domain (protocol/www/path stripped), else a
    normalized name. This is what survives run-to-run so the same rival is the same entry."""
    w = str(website or "").strip().lower()
    if w:
        dom = re.sub(r"^https?://", "", w)
        dom = re.sub(r"^www\.", "", dom).split("/")[0].split("?")[0].strip()
        if dom:
            return dom
    return re.sub(r"[^a-z0-9؀-ۿ]+", "", str(name or "").strip().lower())


def _as_dict(comp: Any) -> dict:
    """Accept either a dict or a CompetitorProfile dataclass.

    `_entry_of` used to call `.get` directly, so a dataclass peer raised AttributeError — latent
    until something passed real discovery objects straight in, which is what the live pipeline
    holds. Found by the user-competitor tests."""
    if isinstance(comp, dict):
        return comp
    import dataclasses
    try:
        return dataclasses.asdict(comp)
    except Exception:  # noqa: BLE001
        return {}


def _entry_of(comp: Any, *, source: str = "discovered") -> dict:
    """One stored line. `source` distinguishes a peer the SYSTEM found and scored from one the
    OWNER named — different kinds of evidence, and this codebase does not let two kinds of evidence
    become indistinguishable downstream."""
    comp = _as_dict(comp)
    sel = comp.get("selection") or {}
    cand = comp.get("candidate") or {}
    name = str(sel.get("name") or cand.get("name") or "").strip()
    site = str(sel.get("website") or cand.get("website") or "").strip()
    return {"key": competitor_key(name, site), "name": name, "website": site, "source": source}


def registry_path(brand_ref: str, *, store_dir: str = _STORE) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", str(brand_ref or "").strip().lower()).strip("_") or "brand"
    return Path(store_dir) / f"{slug}.json"


class RegistryCorrupt(RuntimeError):
    """The registry file EXISTS but is unreadable — we cannot know what the owner approved."""


def load_registry(brand_ref: str, *, store_dir: str = _STORE) -> dict:
    """The persisted registry for a brand, or an empty new one for a first run.

    Raises RegistryCorrupt when the file exists but will not parse (P6 #11c). A corrupt registry
    used to be INDISTINGUISHABLE from a missing one: it returned `entries: []`, so
    `establish_or_diff` read that as "first run, trust everything", AUTO-APPROVED every live peer
    and OVERWROTE the owner's file — the single thing this module promises it never does. A
    truncated write (a killed run) was enough. Refusing keeps the owner's file intact.
    """
    p = registry_path(brand_ref, store_dir=store_dir)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RegistryCorrupt(
                f"{p} exists but is unreadable ({type(exc).__name__}); refusing to re-baseline "
                f"and auto-approve every live peer. Fix or delete the file, then re-run.") from exc
    return {"brand_ref": brand_ref, "entries": []}


def save_registry(registry: dict, *, store_dir: str = _STORE) -> Path:
    p = registry_path(registry.get("brand_ref") or "brand", store_dir=store_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def diff_competitors(registry: dict, discovered: list, *, now: str = "") -> dict:
    """Compare a fresh discovery against the persisted registry WITHOUT mutating it.
    Returns {still_present, disappeared, new_pending} lists of entries — the non-silent
    contract: a newcomer is 'pending' (needs owner approval), a missing baseline peer is
    'disappeared' (flagged, not deleted)."""
    reg_entries = {e["key"]: e for e in (registry.get("entries") or []) if e.get("key")}
    found = {}
    for comp in (discovered or []):
        e = _entry_of(comp)
        if e["key"]:
            found[e["key"]] = e
    approved = {k: e for k, e in reg_entries.items() if e.get("status", "approved") != "removed"}
    still_present = [reg_entries[k] for k in approved if k in found]
    disappeared = [reg_entries[k] for k in approved if k not in found]
    new_pending = [found[k] for k in found if k not in reg_entries]
    return {"still_present": still_present, "disappeared": disappeared,
            "new_pending": new_pending}


def establish_or_diff(brand_ref: str, discovered: list, *, store_dir: str = _STORE,
                      now: str = "") -> dict:
    """The live entrypoint. If no registry exists, ESTABLISH the baseline from this discovery
    (all approved — the first run is trusted) and persist it. Otherwise DIFF (read-only) and
    return the diff + the approved baseline for SWOT. Never auto-adds a newcomer."""
    reg = load_registry(brand_ref, store_dir=store_dir)
    if not (reg.get("entries") or []):
        entries = []
        seen = set()
        for comp in (discovered or []):
            e = _entry_of(comp)
            if e["key"] and e["key"] not in seen:
                seen.add(e["key"])
                e.update({"status": "approved", "first_seen": now, "last_seen": now})
                entries.append(e)
        reg = {"brand_ref": brand_ref, "entries": entries}
        save_registry(reg, store_dir=store_dir)
        return {"established": True, "diff": {"still_present": entries, "disappeared": [],
                "new_pending": []}, "approved_keys": [e["key"] for e in entries]}
    diff = diff_competitors(reg, discovered, now=now)
    approved_keys = [e["key"] for e in (reg.get("entries") or [])
                     if e.get("status", "approved") != "removed"]
    return {"established": False, "diff": diff, "approved_keys": approved_keys}


def approved_competitors(discovered: list, approved_keys: list) -> list:
    """Filter a live discovery down to the APPROVED registry keys — what SWOT should compute
    against (never the raw live pool). Order preserved."""
    keys = set(approved_keys or [])
    return [c for c in (discovered or []) if _entry_of(c)["key"] in keys]


def stable_competitors(brand_ref: str, discovered: list, *, store_dir: str = _STORE,
                       now: str = "") -> tuple[list, dict]:
    """(competitors_for_SWOT, registry_diff) — the ONE way to obtain a peer set.

    Both entrypoints MUST route through this: the CLI (competitor/full_run.py) and the API /
    dashboard (api/routes/swot.py). `approved_competitors` says it plainly — SWOT computes
    against the APPROVED baseline, "never the raw live pool" — but the API used to skip the
    registry entirely, so the PAID deliverable re-ran discovery every time and the
    non-determinism the owner caught on 2026-07-12 stayed live for the client (P6 #11a).

    On a first run the baseline is established from this discovery and it IS the peer set.
    Afterwards the approved set is authoritative EVEN WHEN EMPTY — `if approved:` was the bug
    (P6 #11b): rejecting every peer via HITL yields [], which is falsy, so the raw pool came back
    and the rival the owner explicitly removed reappeared in the next SWOT.

    Raises RegistryCorrupt if the persisted file is unreadable (never silently re-baselines).
    """
    reg = establish_or_diff(brand_ref, discovered, store_dir=store_dir, now=now)
    if reg["established"]:
        peers = list(discovered or [])
    else:
        peers = approved_competitors(discovered, reg["approved_keys"])
    return _with_user_competitors(brand_ref, peers, store_dir=store_dir), reg["diff"]


# ---------------------------------------------------------------------------
# Competitors the OWNER named
# ---------------------------------------------------------------------------

def _user_entries(brand_ref: str, *, store_dir: str = _STORE) -> list:
    try:
        reg = load_registry(brand_ref, store_dir=store_dir)
    except RegistryCorrupt:
        return []
    return [e for e in (reg.get("entries") or []) if e.get("source") == "user"]


def _approved_entries(brand_ref: str, *, store_dir: str = _STORE) -> list:
    """Every entry the owner has NOT removed — whoever originally found it."""
    try:
        reg = load_registry(brand_ref, store_dir=store_dir)
    except RegistryCorrupt:
        return []
    return [e for e in (reg.get("entries") or [])
            if e.get("status", "approved") != "removed" and str(e.get("name") or "").strip()]


def _with_user_competitors(brand_ref: str, peers: list, *, store_dir: str = _STORE) -> list:
    """Re-hydrate the APPROVED baseline into any peer set, from the registry itself.

    The owner's own competitors must survive every discovery outcome, and the failure mode is not
    hypothetical: measured 2026-07-25, all five registry files on this machine hold `entries: []`,
    which `establish_or_diff` reads as "first run — trust this discovery", so a run that found
    nothing returned an empty peer set and the competitor the owner typed silently vanished from
    her own SWOT.

    WIDENED 2026-08-01 FROM `source == "user"` TO EVERY APPROVED ENTRY, because the narrower
    version made this module's central promise untrue. `approved_competitors` is a FILTER over the
    live discovery, so an approved peer could only come back if that discovery found it AGAIN — the
    approved baseline was never authoritative, the live pool was a hard gate on it. The docstring
    said the opposite: "SWOT computes against the APPROVED baseline, never the raw live pool."

    MEASURED on AlFouad the day this changed: the registry held three approved peers (Bloom
    Pharmacy, Sallypharmacies, Egyptian Drug Store), discovery returned zero because the Google
    project was billing-blocked, and the SWOT came out with peers=0 — so weaknesses=0 and
    threats=1, because both of those quadrants exist only by comparison. Across all six stored
    brands the same shape: strengths 4-11, weaknesses 1-2, threats 0-1.

    The registry entry carries name + website, which is everything `competitor_from_user` needs —
    it is named for its first caller, not for a restriction. A peer with a dead site still yields
    `has_scrapable_site=False` and the matrix skips its cells rather than guessing them.

    A REMOVED peer still never returns: `_approved_entries` excludes `status == "removed"`, which
    is the guarantee (P6 #11b) this module exists to keep.
    """
    from .manual import _WHY_RESTORED, competitor_from_user

    out = list(peers or [])
    have = {_entry_of(_as_dict(p))["key"] for p in out}
    for e in _approved_entries(brand_ref, store_dir=store_dir):
        if e.get("key") in have:
            continue
        # SAY WHICH ONE IT IS. An entry the owner TYPED and one an earlier discovery FOUND are
        # both approved and both belong here, but they are not the same claim, and the sentence
        # ships to the client on the peer's selection record.
        peer = competitor_from_user(
            e.get("name", ""), e.get("website", ""),
            why="" if e.get("source") == "user" else _WHY_RESTORED)
        if peer is not None:
            out.append(peer)
            have.add(e["key"])
    return out


def add_user_competitor(brand_ref: str, *, name: str, website: str = "",
                        store_dir: str = _STORE, now: str = "") -> Optional[dict]:
    """Record a competitor the owner named. Returns the stored entry, or None if unusable.

    Naming it IS the approval — the HITL step this module protects exists so the SYSTEM cannot add
    a rival behind the owner's back, and this is the owner speaking. So the key goes into
    `approved_keys` as well as `entries`."""
    from .manual import competitor_from_user

    peer = competitor_from_user(name, website)
    if peer is None:
        return None
    entry = _entry_of(_as_dict(peer), source="user")
    if not entry["key"]:
        return None
    entry["added_at"] = now or _now()

    try:
        reg = load_registry(brand_ref, store_dir=store_dir)
    except RegistryCorrupt:
        return None
    reg.setdefault("brand_ref", brand_ref)
    entries = [e for e in (reg.get("entries") or []) if e.get("key") != entry["key"]]
    entries.append(entry)
    reg["entries"] = entries
    approved = [k for k in (reg.get("approved_keys") or []) if k != entry["key"]]
    approved.append(entry["key"])
    reg["approved_keys"] = approved
    save_registry(reg, store_dir=store_dir)
    return entry


def remove_user_competitor(brand_ref: str, key: str, *, store_dir: str = _STORE) -> bool:
    """Drop a competitor by key. True if something was removed, False if it was not there."""
    key = str(key or "").strip()
    if not key:
        return False
    try:
        reg = load_registry(brand_ref, store_dir=store_dir)
    except RegistryCorrupt:
        return False
    entries = reg.get("entries") or []
    kept = [e for e in entries if e.get("key") != key]
    if len(kept) == len(entries):
        return False
    reg["entries"] = kept
    reg["approved_keys"] = [k for k in (reg.get("approved_keys") or []) if k != key]
    reg.setdefault("brand_ref", brand_ref)
    save_registry(reg, store_dir=store_dir)
    return True


def list_competitors(brand_ref: str, *, store_dir: str = _STORE) -> list:
    """Every stored competitor with its provenance — what the owner's UI lists."""
    try:
        reg = load_registry(brand_ref, store_dir=store_dir)
    except RegistryCorrupt:
        return []
    return [{**e, "source": e.get("source") or "discovered"}
            for e in (reg.get("entries") or []) if e.get("key")]


def brand_ref_from_profile(profile: Any) -> str:
    """A stable registry key for a profile — the brand's own site URL, else its name.

    The API/dashboard receives only a profile (no typed URL), while the CLI passes `args.url`.
    Both must reach a registry, or the paid deliverable falls back to the raw live pool (P6 #11a).

    CAVEAT, stated rather than hidden: if the CLI's typed URL and the profile-derived URL differ in
    form (e.g. a `www.` prefix), the two entrypoints keep SEPARATE baselines. Each is still stable
    run-to-run — which is what kills the drift — they just do not share approvals. Converging them
    would mean re-slugging, which would orphan existing baselines and silently re-approve peers the
    owner had removed: the exact bug this module exists to prevent. Not worth it.
    """
    p = profile if isinstance(profile, dict) else {}
    p = p.get("profile", p) if isinstance(p.get("profile"), dict) else p
    for key in ("website", "url", "canonical_url"):
        v = p.get(key)
        v = v.get("value") if isinstance(v, dict) else v
        if isinstance(v, str) and v.strip():
            return v.strip()
    # fall back to the homepage recorded in the name field's evidence trail
    name = p.get("name")
    if isinstance(name, dict):
        for ev in (name.get("evidence") or []):
            if isinstance(ev, dict) and str(ev.get("page_url") or "").strip():
                return str(ev["page_url"]).strip()
        v = name.get("value")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(name or "brand")
