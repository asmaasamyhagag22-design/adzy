# -*- coding: utf-8 -*-
"""Somebody watches the finished FILM and asks whether it is a good ADVERTISEMENT.

OWNER, on a reel that passed every gate the system owns (2026-08-01):

    «الريل بشع، القشرة أوفر موت، ودار الفوائد صيدلية في مصر وعندها عربي — عملت إنجليزي ليه؟
     والمتصفح المفتوح باين إنه مش بتاعهم… وفجأة اتحل من غير ما يمسك أي منتج»

Five defects, spotted in about five seconds. Every one of them shipped, and the reason is
structural rather than a missing rule: this pipeline has four gates and each judges a PART.

    seed QA          judges one still
    scene QA         judges one clip
    coherence lint   judges the written plan
    final_screening  judges the delivered container — letterboxing, silence, endcard

`final_screening` is the closest thing to this module and it is deliberately code-only: it asks
whether the FILE is broken. Nothing has ever watched the assembled film and asked whether the AD
is any good — whether the product caused anything, whether the problem is shown in a way a person
wants to look at, whether it speaks the audience's language, whether everything on screen belongs
to the brand.

Five parts can each pass and the film still be unwatchable. That is not a new failure class; it
is the one this repo keeps paying for — "the parts were checked, the whole was not" — arriving at
the last place it had not yet been closed.

WHAT THIS IS NOT. It is not a taste engine and it does not score beauty. Every axis below is a
question with a checkable answer that a human reviewer would give the same way twice, and the
verdict is RECOMPUTED IN CODE (PD-1) so the model cannot pass itself. It runs ONCE per film, on
frames already on disk, after everything else has had its say.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Frames spread across the WHOLE film, because the questions are about the arc: a critic shown
# only the opening cannot tell you whether the product caused the ending.
FRAMES = 8


class FilmVerdict(BaseModel):
    """Every axis is a defect the owner actually caught on a shipped reel."""

    # THE CAUSAL CHAIN. «وفجأة اتحل من غير ما يمسك أي منتج» — problem, then resolution, with
    # nothing in between. An ad whose product causes nothing is a mood film with a logo on it.
    product_causes_resolution: bool = True
    # THE PROBLEM'S DEPICTION. «القشرة أوفر موت» — advertising sells the FEELING of a problem,
    # never a clinical exhibit of the symptom. Flakes on a navy shirt repel; they do not persuade.
    problem_is_attractive: bool = True
    # THE AUDIENCE'S LANGUAGE. «صيدلية في مصر وعندها عربي — عملت إنجليزي ليه؟»
    speaks_audience_language: bool = True
    # OWNERSHIP. «المتصفح المفتوح باين إنه مش بتاعهم» — an invented UI, another company's site,
    # a competitor's pack. The gates rejected GARBLED text and never asked whose property it was.
    everything_belongs_to_brand: bool = True
    # AD OR DEMO. «فيديو عملية ومش سريعة كده» — a procedure explains; an ad makes you want.
    reads_as_an_ad: bool = True

    score: int = 7                      # 1..10, an ad director's overall
    reason: str = ""
    fixes: list[str] = []               # what to change, in the order that matters
    fix_scenes: list[int] = []          # 1-based scene per fix (0 = the whole film)
    # TRI-STATE: None = nobody watched. A film we could not screen is never reported as screened.
    passed: Optional[bool] = None
    checked: bool = False


class _CriticResponse(BaseModel):
    product_causes_resolution: bool
    problem_is_attractive: bool
    speaks_audience_language: bool
    everything_belongs_to_brand: bool
    reads_as_an_ad: bool
    score: int
    reason: str
    fixes: list[str] = []
    # WHICH SCENE each fix belongs to, 1-based and parallel to `fixes`. Without this a fix is a
    # sentiment somebody has to route by hand, and "make the ending land" cannot be applied to
    # anything. 0 means "the film as a whole".
    fix_scenes: list[int] = []


def _recompute(r: Any) -> bool:
    """PD-1: the verdict is computed HERE, never taken from the model.

    The four hard gates are the ones the owner rejected a whole reel over. `reads_as_an_ad` and
    the score are craft — they lower the grade and inform the fixes without blocking on their
    own, because "this feels procedural" is a note, not a defect."""
    return bool(r.product_causes_resolution and r.problem_is_attractive
                and r.speaks_audience_language and r.everything_belongs_to_brand
                and int(r.score or 0) >= 6)


def _system(brand: str, audience_lang: str, country: str) -> str:
    aud = (audience_lang or "").strip() or "the brand's own market"
    where = f" in {country}" if country else ""
    return (
        "You are a world-class advertising CREATIVE DIRECTOR watching a finished commercial for "
        f"'{brand}'{where} for the first time. You are not reviewing footage; you are deciding "
        "whether this is an ad the brand can run.\n"
        "You will be shown frames sampled ACROSS THE WHOLE FILM, in order, plus its voiceover and "
        "on-screen copy. Judge the FILM, not any single frame.\n\n"
        "- product_causes_resolution: true ONLY if the brand's product or service visibly CAUSES "
        "the change the film promises. A film that opens on a problem and ends on a happy person "
        "with nothing in between — nobody holding, using, taking or receiving the product — is "
        "FALSE. This is the single most common way an ad becomes a mood film with a logo.\n"
        "- problem_is_attractive: true if the problem is dramatised in a way a viewer WANTS to "
        "keep watching — implied, felt, in a beautiful frame. FALSE if it is shown as a clinical "
        "or physically off-putting exhibit (visible flakes, sores, stains, close-up affliction). "
        "Advertising sells the feeling of a problem, never the symptom.\n"
        f"- speaks_audience_language: the audience{where} speaks {aud}. TRUE only if the "
        "VOICEOVER and the ON-SCREEN COPY are in that language. A brand whose website has an "
        "English interface does not make its customers English speakers.\n"
        "- everything_belongs_to_brand: true if every logo, screen, package, app, website and "
        "sign in frame is THIS brand's own. FALSE for an invented interface, a generic browser "
        "showing something that is not their site, another company's product, or made-up "
        "branding. Legible or garbled is not the question — WHOSE is.\n"
        "- reads_as_an_ad: true if this persuades. FALSE if it reads as a tutorial, a product "
        "demo, a procedure, or a sequence of statements with no want in it.\n"
        "- score: 1-10. Would a brand of this standing actually run this film?\n"
        "- reason: one sentence naming the WORST thing about it.\n"
        "- fixes: up to 3 concrete changes, worst first. Each names what to change, not a "
        "sentiment — 'have her take the sachet from the pharmacist' beats 'make it warmer'. "
        "Write each as an INSTRUCTION TO THE SCENE it belongs to, so it can be applied without "
        "you present.\n"
        "- fix_scenes: the 1-based SCENE NUMBER each fix belongs to, in the same order as fixes. "
        "Use 0 when a fix is about the film as a whole (its language, its pace, its ending). The "
        "scene list is given below; count from it, not from the frames."
    )


def _sample(video: "str | Path", n: int) -> list[bytes]:
    """Frames evenly spread across the film, as PNG bytes. [] on anything unreadable."""
    from reel.ffmpeg_tools import run_ffmpeg
    import tempfile
    src = Path(str(video))
    if not src.is_file():
        return []
    out: list[bytes] = []
    try:
        from reel.motion import _clip_duration
        dur = float(_clip_duration(src) or 0.0)
    except Exception:  # noqa: BLE001
        dur = 0.0
    if dur <= 0:
        return []
    with tempfile.TemporaryDirectory(prefix="critic_") as td:
        for i in range(n):
            # Inset from both ends: the very first and last frames are mid-fade and tell a critic
            # about the transition rather than about the film.
            t = dur * (0.06 + 0.88 * (i / max(1, n - 1)))
            p = Path(td) / f"f{i}.png"
            try:
                run_ffmpeg(["-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", str(p)])
                if p.is_file() and p.stat().st_size > 512:
                    out.append(p.read_bytes())
            except Exception:  # noqa: BLE001 — a missed frame is not a failed screening
                continue
    return out


def critique_film(video: "str | Path", *, caller: Any = None, brand: str = "",
                  audience_language: str = "", country: str = "",
                  voiceover: str = "", captions: Optional[list] = None,
                  scenes: Optional[list] = None) -> FilmVerdict:
    """Watch the assembled film and judge it as an advertisement.

    Returns `checked=False` when no critic could run — a film nobody watched is never reported as
    watched. Never raises: the last viewer before the owner must not be the thing that breaks the
    delivery."""
    if caller is None:
        return FilmVerdict(reason="no critic caller: the film was not watched")
    frames = _sample(video, FRAMES)
    if len(frames) < 3:
        return FilmVerdict(reason=f"only {len(frames)} frame(s) readable — the film was not watched")

    caps = [str(c).strip() for c in (captions or []) if str(c or "").strip()]
    # THE SCENE LIST IS WHAT MAKES A FIX ROUTABLE. Frames have no numbers; scenes do. Without it
    # the critic can only say "the ending does not land", which nobody and nothing can apply.
    lines = []
    for i, sc in enumerate(scenes or [], start=1):
        what = " ".join(str(getattr(sc, "veo_prompt", "") or "").split())[:150]
        vo = " ".join(str(getattr(sc, "voiceover", "") or "").split())[:90]
        lines.append(f"  scene {i}: {what}" + (f" | VO: {vo}" if vo else ""))
    user = (
        f"VOICEOVER (verbatim): {voiceover.strip() or '(none)'}\n"
        f"ON-SCREEN COPY, in order: {' | '.join(caps) if caps else '(none)'}\n"
        + ("THE SCENES, in order — number your fixes against THIS list:\n"
           + "\n".join(lines) + "\n" if lines else "")
        + f"{len(frames)} frames follow, evenly spaced from the start of the film to its end.\n"
        "Watch it as a whole and return the verdict."
    )
    try:
        resp, _u = caller(_system(brand, audience_language, country), user, _CriticResponse,
                          group_name="film_critic",
                          images=[(f, "image/png") for f in frames])
    except Exception as exc:  # noqa: BLE001
        return FilmVerdict(reason=f"critic call failed ({type(exc).__name__})")

    v = FilmVerdict(
        product_causes_resolution=bool(resp.product_causes_resolution),
        problem_is_attractive=bool(resp.problem_is_attractive),
        speaks_audience_language=bool(resp.speaks_audience_language),
        everything_belongs_to_brand=bool(resp.everything_belongs_to_brand),
        reads_as_an_ad=bool(resp.reads_as_an_ad),
        score=int(resp.score or 0),
        reason=(resp.reason or "").strip(),
        fixes=[str(f).strip() for f in (resp.fixes or []) if str(f).strip()][:3],
        fix_scenes=[int(i or 0) for i in (getattr(resp, "fix_scenes", None) or [])][:3],
        checked=True,
    )
    v.passed = _recompute(resp)
    logger.info("[film] %s (score %s) :: %s",
                "PASS" if v.passed else "FAIL", v.score, v.reason[:120])
    return v


def failed_axes(v: FilmVerdict) -> list[str]:
    """The hard gates this film failed, named the way the owner named them."""
    out = []
    if not v.product_causes_resolution:
        out.append("the product never causes the resolution")
    if not v.problem_is_attractive:
        out.append("the problem is shown as an off-putting symptom rather than a feeling")
    if not v.speaks_audience_language:
        out.append("the film does not speak the audience's language")
    if not v.everything_belongs_to_brand:
        out.append("something on screen is not the brand's own")
    return out
