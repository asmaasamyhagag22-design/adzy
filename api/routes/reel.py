"""POST /api/reel/from-profile — the Studio's reel, rendered by the SAME engine as the CLI.

ONE ENGINE (2026-07-26). This route used to call `reel.generate.build_brand_generated_reel`
(art-director story -> Imagen stills -> **Veo** i2v). The CLI default has since become
`reel.creative.render_creative_reel`: the creative director's AD SPINE, gate-passed 9:16 seeds
conditioned on the brand's REAL product photos, ONE protagonist across scenes, the per-scene
motion contract, a vocalized voice-over, the branded end-card, and a final screening of the
assembled film. Every one of those fixes was paid for and none of them reached the dashboard —
which still held a stale engine pin.

So the route no longer has an engine of its own. It resolves the same provider the CLI resolves
(`default_video_provider`, Veo-only), calls the same entry, and adds only what a WEB caller
needs: the mp4 as base64, the post-ready caption, and the screening verdict. The real-photo
Motion engine stays as the honest fallback when the director cannot run at all.

SYNC `def` (ffmpeg / Playwright sync cannot run in the event loop; FastAPI runs it in a worker
thread). Returns the mp4 as base64.
"""
from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from reel.schemas import REEL_H, REEL_W, ReelFromProfileRequest, ReelFromProfileResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _probe_duration(path) -> float:
    from reel.text_overlay import _probe_duration as _p
    try:
        return float(_p(path) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _screen(out: Path) -> str:
    """The same viewer the CLI runs before it ships. A web caller cannot be blocked by a
    non-zero exit code, so the verdict travels in the response instead — never absent, so a
    reel nobody could screen is not reported as a clean one."""
    try:
        from reel.final_screening import screen_reel
        v = screen_reel(out, log=lambda m: logger.info("[screening] %s", m))
    except Exception:  # noqa: BLE001
        return "not screened"
    if not v.checked:
        return "not screened"
    return "clean" if v.passed else "issues: " + "; ".join(v.issues)


def _caption(profile: dict, language: str):
    try:
        from api.webapp_social_copy import social_caption
        out = social_caption(profile, language=language)
        return str(out.get("caption") or ""), list(out.get("hashtags") or [])
    except Exception:  # noqa: BLE001 — a caption failure must never cost the reel
        logger.exception("reel caption failed")
        return "", []


@router.post("/reel/from-profile", response_model=ReelFromProfileResponse)
def create_reel_from_profile(request: ReelFromProfileRequest) -> ReelFromProfileResponse:
    profile = request.profile
    out_dir = Path("outputs/reels")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"web_reel_{uuid.uuid4().hex[:8]}.mp4"

    name, palette, warnings = "", [], []
    brief = None
    try:
        from reel.from_profile import build_reel_brief
        brief = build_reel_brief(profile)
        name = brief.business_name
        palette = list(brief.palette_hex or [])
        warnings = list(brief.warnings or [])
    except Exception:  # noqa: BLE001 — brief is best-effort metadata
        pass

    mode = ""
    creative = None
    # 1) THE DIRECTOR — identical to `python -m reel <profile>` with no flags.
    try:
        if brief is None:
            raise RuntimeError("no reel brief could be built from this profile")
        from reel.creative import render_creative_reel
        from reel.image_quality import filter_usable_photos
        from reel.video_provider import default_video_provider

        imgs = (profile.get("visual") or {}).get("content_images") or []
        photos = filter_usable_photos(imgs, max_keep=max(2, request.n_scenes + 2)) or imgs
        _res, creative = render_creative_reel(
            profile, brief, photos,
            provider=default_video_provider(), out_path=out,
            n_scenes=max(3, request.n_scenes), language=request.language,
            with_voiceover=True,
        )
        mode = "creative"
    except Exception as gen_exc:  # noqa: BLE001
        logger.exception("creative reel failed; falling back to the motion engine")
        # 2) FALLBACK: the Motion engine over the brand's real scraped photos. NAMED in the
        #    warnings — a degraded reel that looks like the directed one is a lie.
        try:
            from reel.image_quality import filter_usable_photos
            from reel.motion import build_motion_reel
            imgs = (profile.get("visual") or {}).get("content_images") or []
            usable = filter_usable_photos(imgs, max_keep=max(2, request.n_scenes + 2))
            if not usable:
                raise RuntimeError(
                    f"the creative director failed and there are no usable scraped photos "
                    f"to fall back on ({gen_exc})")
            build_motion_reel(usable, out, palette=palette, music_path=None)
            mode = "motion"
            warnings.append(
                "المخرج الإبداعي فشل — الريل ده اتعمل من صور الموقع الحقيقية (Ken Burns) "
                f"مش بالمسار الكامل: {gen_exc}")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Reel generation failed: {exc}") from exc

    # The branded END-CARD closes the reel. Its outcome is stamped, never swallowed — the v2
    # card failed on a dims mismatch and the owner found out from the screen.
    endcard_ok = False
    try:
        from reel.endcard import append_endcard_to_reel
        endcard_ok = bool(append_endcard_to_reel(profile, out))
    except Exception:  # noqa: BLE001
        endcard_ok = False
    if not endcard_ok:
        warnings.append("الريل اتعمل من غير كارت النهاية (اللوجو).")
    try:
        import json as _json
        out.with_suffix(".path.json").write_text(
            _json.dumps({"path": mode, "endcard": "appended" if endcard_ok else "failed"},
                        ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    # THE DELIVERY CONTAINER, decided once for BOTH paths (directive §2). The CLI does the same
    # thing at the same point; a fix on one path only is how the dashboard ended up on its own
    # reel engine in the first place. Measured on the last shipped reel: 772x1372, yuvj420p,
    # full-range, bt470bg, 44.1 kHz — every field wrong, and the platform's transcoder would
    # have crushed the blacks of an already-dark film.
    envelope = {"value": None, "reason": "not finalised"}
    try:
        from reel.encode_profile import finalize_delivery
        envelope = finalize_delivery(out)
    except Exception:  # noqa: BLE001
        logger.exception("finalize_delivery unavailable")
    if envelope.get("value") is False:
        warnings.append("الحاوية مش مطابقة لمواصفات المنصة: " + str(envelope.get("reason") or ""))
    elif envelope.get("value") is None:
        warnings.append("الحاوية ماتظبطتش (الملف اتسلّم زي ما هو).")

    try:
        data = out.read_bytes()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Reel render produced no file: {exc}") from exc

    # THE AUDIT TRAIL, for web reels too. `build_reel_audit` existed and was correct-shaped, and
    # this route had never called it — every reel the owner generated from the dashboard shipped
    # with no exportable proof of anything. The envelope and loudness verdicts join it as gates,
    # so the single `artifact_verdict` covers the container as well as the content.
    audit_path = None
    verdict = {"value": None, "reason": "audit unavailable"}
    try:
        from reel.audit import build_reel_audit, write_reel_audit
        from reel.encode_profile import check_audio
        from reel.verdicts import gate

        gates = [gate(**{k: v for k, v in envelope.items()
                         if k in ("value", "method", "measure", "threshold", "observed",
                                  "units_checked", "reason")}, name="platform_envelope")]
        _aud = check_audio(out)
        gates.append(gate("audio_loudness", value=_aud.get("value"), method="code",
                          measure="lufs", threshold=_aud.get("threshold"),
                          observed=_aud.get("observed"),
                          units_checked=_aud.get("units_checked") or {},
                          reason=_aud.get("reason") or ""))
        audit = build_reel_audit(profile, creative, render_result=_res if mode == "creative"
                                 else None, extra_gates=gates)
        verdict = audit.get("artifact_verdict") or verdict
        audit_path = write_reel_audit(audit, out)
    except Exception:  # noqa: BLE001 — an audit failure must not cost the owner her reel
        logger.exception("reel audit failed")
    if verdict.get("value") is False:
        warnings.append("الحكم النهائي: " + str(verdict.get("reason") or ""))

    lang = request.language if request.language in ("ar", "en") else "auto"
    caption, tags = _caption(profile, lang)
    screening = _screen(out)
    if screening.startswith("issues"):
        warnings.append(f"مراجعة العرض الأخير: {screening[8:]}")

    return ReelFromProfileResponse(
        video_base64=base64.b64encode(data).decode("ascii"),
        filename=out.name,
        width=REEL_W,
        height=REEL_H,
        duration_s=_probe_duration(out),
        scene_count=len(getattr(creative, "scenes", []) or []) or request.n_scenes,
        mode=mode,
        business_name=name,
        warnings=warnings,
        social_caption=caption,
        hashtags=tags,
        screening=screening,
        artifact_verdict=("ok" if verdict.get("value") is True
                          else ("unverified" if verdict.get("value") is None
                                else "blocked: " + str(verdict.get("reason") or ""))),
        envelope=("ok" if envelope.get("value") is True
                  else ("not finalised" if envelope.get("value") is None
                        else "issues: " + str(envelope.get("reason") or ""))),
    )
