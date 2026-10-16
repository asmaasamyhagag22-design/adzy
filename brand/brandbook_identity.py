"""Canonical BrandCreativeDNA identity — one brandbook per `domain_key`, never per display name.

MEASURED DEFECT (2026-09-01). `poster/pipeline.py` addressed the brandbook by slugging the
brand's DISPLAY NAME:

    re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "brand"

An all-Arabic name has no `[a-z0-9]` at all, so the whole string erased to the empty match and
the `or "brand"` fallback fired. Three stored brands therefore shared ONE file:

    elabdfoods_com   "حلواني العبد…"          -> outputs/brandbooks/brand_dna.json
    marasimltd_com   "مراسم للحلول الرقمية"    -> outputs/brandbooks/brand_dna.json
    nahdionline_com  "صيدلية النهدي"           -> outputs/brandbooks/brand_dna.json

That file holds Al Nahdi's DNA (all 8 `references_seen` are nahdionline.com), so a Marasim
render read a Saudi retail pharmacy's visual authority — "dusty blue (#4A7C9B) … primary CTA
button", "turquoise water", a discount number "3-4x larger than the headline", a "Shop Now"
button rule — and obeyed it faithfully. Marasim's real DNA (warm sepia / golden hour, black
field, fiery orange #F04B1E, Cairo/Giza, "no blues, greens, or purples") sat un-read on disk
under its old English-name slug, orphaned the moment the scraped name came back in Arabic.

THE RULE. `domain_key` is the identity. It already names the stored brand object
(`outputs/webapp/brands/<domain_key>.json`), it is derivable from the profile's own
`final_url`/`source_url` without consulting a name, and it is stable across every rename and
every language. Display names are mutable, localized, LLM-touched presentation data and are
recorded INSIDE the artifact for readability — never in the filename.

Transliteration stays available for humans (`readable_label`), but it never decides authority.

NO SILENT REBUILD. A missing brandbook used to be indistinguishable from an unresolvable one,
so the identity break healed itself into a fresh (wrong) DNA and left no trace. Identity
failure is now a typed outcome, separate from an honest first-time cache miss.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_CACHE_DIR = "outputs/brandbooks"

# The artifact schema that carries embedded identity. Files written before this existed have
# no `domain_key` inside them and are handled by the migration audit, never by a live lookup.
BRANDBOOK_SCHEMA_VERSION = 2

# --- identity resolution states (§15: a fallback that cannot go quiet) -------------------
CANONICAL_DOMAIN_KEY = "CANONICAL_DOMAIN_KEY"
LEGACY_VERIFIED_MIGRATION = "LEGACY_VERIFIED_MIGRATION"
LEGACY_AMBIGUOUS_BLOCKED = "LEGACY_AMBIGUOUS_BLOCKED"

# --- typed failures (§8, §10) -----------------------------------------------------------
BRANDBOOK_IDENTITY_UNRESOLVED = "BRANDBOOK_IDENTITY_UNRESOLVED"
BRANDBOOK_LEGACY_COLLISION = "BRANDBOOK_LEGACY_COLLISION"
BRANDBOOK_AUTHORITY_MISMATCH = "BRANDBOOK_AUTHORITY_MISMATCH"


class BrandbookIdentityError(Exception):
    """Identity could not be established — NOT an ordinary cache miss.

    `code` is one of the BRANDBOOK_* constants above so a caller can branch on the kind of
    failure instead of pattern-matching a message.
    """

    def __init__(self, code: str, message: str, *, detail: Optional[dict] = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = detail or {}


# ---------------------------------------------------------------------
# domain_key
# ---------------------------------------------------------------------

def _fv(profile: dict, key: str) -> str:
    """Unwrap an EvidencedField ({value, evidence, …}) or a plain scalar."""
    v = (profile or {}).get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return "" if v is None else str(v)


_SAFE_KEY = re.compile(r"[^a-z0-9_]+")


def normalize_domain_key(domain_key: str) -> str:
    """Filesystem-safe form of an already-`_`-separated key. Mirrors `api.brand_store._safe`
    so the brandbook and the brand object cannot disagree about one brand's key."""
    return _SAFE_KEY.sub("_", (domain_key or "").lower()).strip("_")


def domain_key_for(profile: dict, domain_key: str = "") -> str:
    """The brand's stable identity.

    An explicitly passed `domain_key` (the stored brand object's own field) always wins — it is
    the authority. Otherwise it is derived from the profile's OWN URLs, which are scraped facts
    rather than generated text. The name is never consulted, in any branch.

    Returns "" when nothing name-independent is available; the caller decides whether that is a
    legitimate never-created brand or an unresolved identity.
    """
    explicit = normalize_domain_key(domain_key)
    if explicit:
        return explicit
    try:
        from api.brand_translate import domain_key_from_url
    except Exception:                                   # pragma: no cover - import guard
        return ""
    for key in ("final_url", "source_url", "url", "website"):
        url = _fv(profile, key)
        if not url:
            continue
        derived = normalize_domain_key(domain_key_from_url(url))
        # `domain_key_from_url` answers "brand" for an unparseable URL — the very sentinel that
        # caused this defect. Refuse it rather than inherit a second shared bucket.
        if derived and derived != "brand":
            return derived
    return ""


def readable_label(profile: dict, dna: Any = None) -> str:
    """A human label for logs and optional side-by-side filenames. NEVER an identity.

    Unlike the old `_slug`, this keeps non-ASCII letters instead of erasing them, so an Arabic
    name stays recognisable in a log line. It still must not name a canonical artifact.
    """
    name = _fv(profile, "name") or (getattr(dna, "business_name", "") if dna else "") or ""
    name = unicodedata.normalize("NFKC", str(name)).strip()
    label = re.sub(r"\s+", "_", name)
    label = re.sub(r"[\\/:*?\"<>|]+", "", label)        # path separators only
    return label[:80] or "brand"


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

def canonical_path(domain_key: str, *, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> Path:
    """`outputs/brandbooks/<domain_key>_dna.json` — the one legal write target for a brand."""
    key = normalize_domain_key(domain_key)
    if not key:
        raise BrandbookIdentityError(
            BRANDBOOK_IDENTITY_UNRESOLVED,
            "refusing to build a brandbook path without a domain_key",
        )
    return Path(cache_dir) / f"{key}_dna.json"


def legacy_name_slug(name: str) -> str:
    """The OLD identity function, kept verbatim for one purpose: finding the files it wrote.

    It is never used to choose a write target. `poster/pipeline.py` imported its own copy of
    this; that copy is gone, so there is exactly one definition of the broken behaviour and it
    lives here, next to the migration that undoes it.
    """
    return re.sub(r"[^a-z0-9]+", "_", (name or "brand").lower()).strip("_") or "brand"


def legacy_candidate_paths(profile: dict, *, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> list[Path]:
    """Legacy files this brand's display name WOULD have addressed, for audit only."""
    out: list[Path] = []
    seen: set[str] = set()
    for key in ("name", "organization_name", "business_name"):
        nm = _fv(profile, key)
        if not nm:
            continue
        p = Path(cache_dir) / f"{legacy_name_slug(nm)}_dna.json"
        if str(p) not in seen:
            seen.add(str(p))
            out.append(p)
    return out


# ---------------------------------------------------------------------
# Embedded identity + hashing
# ---------------------------------------------------------------------

# The design fields that ARE the visual authority. The hash covers exactly these, so it is
# stable across metadata edits (a display-name change must not move the DNA hash) and changes
# whenever the actual creative direction changes.
_DNA_CONTENT_FIELDS = (
    "layout_philosophy", "composition_patterns", "typographic_character", "color_usage",
    "imagery_style", "mood", "motifs", "text_density", "signature_moves",
    "do_list", "dont_list",
)


def dna_content_hash(dna: Any) -> str:
    """sha256 over the DNA's creative content only (identity/provenance excluded)."""
    payload: dict[str, Any] = {}
    for f in _DNA_CONTENT_FIELDS:
        v = getattr(dna, f, None) if not isinstance(dna, dict) else dna.get(f)
        if isinstance(v, list):
            payload[f] = [str(x) for x in v]
        else:
            payload[f] = "" if v is None else str(v)
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _get(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def stamp_identity(dna: Any, *, domain_key: str, profile: Optional[dict] = None) -> Any:
    """Write identity + provenance INTO the artifact (§3), leaving the creative fields alone.

    The filename provides identity; these fields make the file self-describing, which is what
    lets `validate_embedded_identity` catch a stray filesystem copy or rename.
    """
    key = normalize_domain_key(domain_key)
    if not key:
        raise BrandbookIdentityError(
            BRANDBOOK_IDENTITY_UNRESOLVED, "cannot stamp a brandbook without a domain_key")
    profile = profile or {}
    display = _fv(profile, "name") or str(_get(dna, "business_name", "") or "")
    source_url = _fv(profile, "final_url") or _fv(profile, "source_url")

    setters = {
        "domain_key": key,
        "display_name": display,
        "source_url": source_url,
        "schema_version": BRANDBOOK_SCHEMA_VERSION,
    }
    for k, v in setters.items():
        try:
            setattr(dna, k, v)
        except Exception:                                # pydantic model without the field
            pass
    try:
        setattr(dna, "dna_hash", dna_content_hash(dna))
    except Exception:
        pass
    if not _get(dna, "generated_at", ""):
        try:
            import time
            setattr(dna, "generated_at", int(time.time()))
        except Exception:
            pass
    return dna


@dataclass
class IdentityVerdict:
    """Why a brandbook was accepted or refused — never a bare bool."""
    ok: bool
    state: str = ""
    code: str = ""
    reason: str = ""
    detail: dict = field(default_factory=dict)


def validate_embedded_identity(payload: Any, requested_key: str, *,
                               path: Optional[Path] = None) -> IdentityVerdict:
    """§10 — the file must not claim to be a different brand than the one asked for.

    A file with NO embedded `domain_key` is a legacy artifact: it cannot prove ownership, so it
    is refused for live use and routed to the migration audit rather than silently trusted.
    """
    want = normalize_domain_key(requested_key)
    got = normalize_domain_key(str(_get(payload, "domain_key", "") or ""))
    if not got:
        return IdentityVerdict(
            False, LEGACY_AMBIGUOUS_BLOCKED, BRANDBOOK_IDENTITY_UNRESOLVED,
            "artifact carries no embedded domain_key (pre-migration file)",
            {"path": str(path or ""), "requested": want})
    if got != want:
        return IdentityVerdict(
            False, LEGACY_AMBIGUOUS_BLOCKED, BRANDBOOK_AUTHORITY_MISMATCH,
            f"artifact belongs to {got!r}, not {want!r}",
            {"path": str(path or ""), "requested": want, "embedded": got})

    # Reference ownership, where the artifact records it. Refs are harvested from many hosts
    # (Behance, adsoftheworld) for legitimate reasons, so a non-matching ref is NOT a failure;
    # a recorded source_url pointing at another domain is.
    src = str(_get(payload, "source_url", "") or "")
    if src:
        try:
            from api.brand_translate import domain_key_from_url
            src_key = normalize_domain_key(domain_key_from_url(src))
        except Exception:
            src_key = ""
        if src_key and src_key != want:
            return IdentityVerdict(
                False, LEGACY_AMBIGUOUS_BLOCKED, BRANDBOOK_AUTHORITY_MISMATCH,
                f"embedded source_url resolves to {src_key!r}, not {want!r}",
                {"path": str(path or ""), "requested": want, "source_url": src})
    return IdentityVerdict(True, CANONICAL_DOMAIN_KEY, "", "identity verified",
                           {"path": str(path or ""), "domain_key": want})


# ---------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------

def _write_atomic(path: Path, payload: str) -> None:
    """Same discipline as `api.brand_store._write_atomic`: a crash mid-write must not leave a
    half-parsed brandbook that a later run would read as authority."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_brandbook(path: str | Path) -> dict:
    """Raw JSON of a brandbook artifact (no schema coercion — the audit reads legacy shapes)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_brandbook(dna: Any, *, domain_key: str, profile: Optional[dict] = None,
                   cache_dir: str | Path = DEFAULT_CACHE_DIR) -> Path:
    """§11 — write a brandbook to ITS OWN canonical path, and nowhere else.

    The destination is computed from `domain_key`, then the stamped artifact is re-checked
    against that same key before the bytes land. One brand cannot overwrite another's
    brandbook even if a caller passes a mismatched pair.
    """
    key = normalize_domain_key(domain_key)
    if not key:
        raise BrandbookIdentityError(
            BRANDBOOK_IDENTITY_UNRESOLVED, "refusing to save a brandbook without a domain_key")
    dna = stamp_identity(dna, domain_key=key, profile=profile)
    path = canonical_path(key, cache_dir=cache_dir)

    verdict = validate_embedded_identity(dna, key, path=path)
    if not verdict.ok:
        raise BrandbookIdentityError(
            verdict.code, f"refusing cross-brand write to {path.name}: {verdict.reason}",
            detail=verdict.detail)

    # A brandbook already at this path must belong to this brand. Refuse rather than clobber.
    if path.exists():
        try:
            prior = read_brandbook(path)
        except (OSError, ValueError):
            prior = None
        if prior is not None:
            prior_key = normalize_domain_key(str(prior.get("domain_key") or ""))
            if prior_key and prior_key != key:
                raise BrandbookIdentityError(
                    BRANDBOOK_AUTHORITY_MISMATCH,
                    f"{path.name} already holds {prior_key!r}; refusing to overwrite with {key!r}",
                    detail={"path": str(path), "existing": prior_key, "incoming": key})

    if hasattr(dna, "model_dump_json"):
        payload = dna.model_dump_json(indent=2)
    else:
        payload = json.dumps(dna if isinstance(dna, dict) else dna.__dict__,
                             ensure_ascii=False, indent=2)
    _write_atomic(path, payload)
    return path


@dataclass
class BrandbookLookup:
    """The outcome of a canonical lookup — an explicit third state, never absence-as-success."""
    domain_key: str
    path: Optional[Path] = None
    exists: bool = False
    state: str = ""
    code: str = ""
    reason: str = ""
    legacy_files: list[str] = field(default_factory=list)


def resolve_brandbook(profile: dict, domain_key: str = "", *,
                      cache_dir: str | Path = DEFAULT_CACHE_DIR) -> BrandbookLookup:
    """§9 — resolve the canonical location from `domain_key` FIRST, and say what was found.

    Three outcomes, deliberately distinguishable (the old code had two and conflated them):
      * canonical file present            -> exists=True, CANONICAL_DOMAIN_KEY
      * no canonical file, no legacy trace -> exists=False, CANONICAL_DOMAIN_KEY (honest miss;
                                              a genuine new brand may build normally)
      * no canonical file BUT legacy DNA evidence exists -> exists=False,
                                              LEGACY_AMBIGUOUS_BLOCKED + a typed code, because
                                              rebuilding here is what hid the identity break.
    """
    key = domain_key_for(profile, domain_key)
    if not key:
        return BrandbookLookup(
            domain_key="", path=None, exists=False, state=LEGACY_AMBIGUOUS_BLOCKED,
            code=BRANDBOOK_IDENTITY_UNRESOLVED,
            reason="no domain_key and no name-independent URL on the profile")

    path = canonical_path(key, cache_dir=cache_dir)
    if path.exists():
        try:
            payload = read_brandbook(path)
        except (OSError, ValueError) as exc:
            return BrandbookLookup(key, path, False, LEGACY_AMBIGUOUS_BLOCKED,
                                   BRANDBOOK_IDENTITY_UNRESOLVED,
                                   f"canonical artifact unreadable: {exc}")
        verdict = validate_embedded_identity(payload, key, path=path)
        if verdict.ok:
            return BrandbookLookup(key, path, True, CANONICAL_DOMAIN_KEY, "", verdict.reason)
        return BrandbookLookup(key, path, False, verdict.state, verdict.code, verdict.reason)

    # Nothing canonical. Is there legacy evidence that this brand HAD a DNA? If so this is an
    # identity failure, not a first run.
    legacy = [str(p) for p in legacy_candidate_paths(profile, cache_dir=cache_dir) if p.exists()]
    if legacy:
        shared = [p for p in legacy if Path(p).name == "brand_dna.json"]
        return BrandbookLookup(
            key, path, False, LEGACY_AMBIGUOUS_BLOCKED,
            BRANDBOOK_LEGACY_COLLISION if shared else BRANDBOOK_IDENTITY_UNRESOLVED,
            ("prior DNA exists under a display-name slug that is shared with other brands"
             if shared else "prior DNA exists under a display-name slug; migrate it first"),
            legacy_files=legacy)

    return BrandbookLookup(key, path, False, CANONICAL_DOMAIN_KEY, "",
                           "no brandbook yet for this brand")


def load_brandbook(profile: dict, domain_key: str = "", *,
                   cache_dir: str | Path = DEFAULT_CACHE_DIR) -> tuple[Optional[dict], BrandbookLookup]:
    """Canonical read. Returns (payload_or_None, lookup) — the lookup always explains itself."""
    lookup = resolve_brandbook(profile, domain_key, cache_dir=cache_dir)
    if not lookup.exists or lookup.path is None:
        return None, lookup
    try:
        return read_brandbook(lookup.path), lookup
    except (OSError, ValueError) as exc:
        lookup.exists = False
        lookup.state = LEGACY_AMBIGUOUS_BLOCKED
        lookup.code = BRANDBOOK_IDENTITY_UNRESOLVED
        lookup.reason = f"canonical artifact unreadable: {exc}"
        return None, lookup
