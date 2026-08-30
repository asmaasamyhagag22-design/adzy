"""A strong, unmistakable BRAND END-CARD for the reel (the owner: "فين WE؟").

The AI-generated footage is deliberately TEXT-FREE (Imagen/Veo bake garbled text/logos), so the
brand identity can't live INSIDE the scenes — it must come from a COMPOSITED overlay we control.
This appends a clean ~2.5s end-card — the real brand LOGO big, on the brand-colour, with the CTA —
so the reel ends on a clear "this is WE" moment. Rendered via Chromium (Arabic-safe), then
xfade-appended to the finished reel. Best-effort: returns False and leaves the reel untouched on
any failure. Never raises.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from reel.ffmpeg_tools import run_ffmpeg
# ONE PLACE DECIDES HOW LONG THE CLOSE IS. The card's length and its overlap are part of the
# delivered runtime, so they are owned by `reel/runtime.py` — a second literal here is how the
# budget and the card drift apart, which is the arithmetic that made every 15-second reel 15.3-17.6.
from reel.runtime import ENDCARD_S, ENDCARD_XFADE_S
from reel.schemas import REEL_H, REEL_W
from reel.textlayer import _FONTS_LINK


# THE ERROR HANDLER USED TO BE THE ERROR. `logger` was referenced twice in this module — once
# when the reel's dimensions cannot be probed, once in the catch-all that reports a failed
# append — and never defined. So both paths raised NameError from INSIDE the handler: the code
# written to explain a lost end-card was the code that hid the reason for it.
logger = logging.getLogger(__name__)


# ----- small colour helpers (contrast-aware background) -----
def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    s = (h or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (22, 20, 26)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (22, 20, 26)


def _rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _mix(a, b, t: float):
    return tuple(a[i] * (1 - t) + b[i] * t for i in range(3))


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _logo_mean_luma(logo_url: Optional[str]) -> float:
    """Average luminance (0-255) of the logo's NON-transparent pixels — decides whether the card
    goes dark (a light/gold/white logo pops on charcoal — the real luxury look) or light (a dark
    wordmark pops on ivory). Missing/undecodable logo -> treat as light (255) -> dark card."""
    if not logo_url:
        return 255.0
    try:
        import io

        from PIL import Image

        from reel.video_provider import _load_reference_image
        loaded = _load_reference_image(logo_url)
        if not loaded:
            return 255.0
        data, _mime = loaded
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im.thumbnail((160, 160))
        vis = [(r, g, b) for (r, g, b, a) in im.getdata() if a > 40]
        if not vis:
            return 255.0
        return sum(_luma(p) for p in vis) / len(vis)
    except Exception:
        return 255.0


def _endcard_html(logo_uri: Optional[str], name: str, cta: str, url_label: str,
                  accent: str, accent2: str, rtl: bool, width: int, height: int,
                  *, elegant: bool, dark: bool, ground_uri: str = "") -> str:
    ac = _hex_to_rgb(accent)
    if ground_uri:
        # THE END CARD BELONGS TO THE FILM (owner, 2026-07-28: «الخاتمة تبقى مميزة بالجو بردو
        # بالألوان الطبيعية… متخليهوش أبيض لأنها خلصت كتير قبل كده»).
        #
        # Both synthetic branches below are cards: a gradient the reel has never seen, arriving
        # after eighteen seconds of a real place. The ivory one in particular ended reel after
        # reel on the same white, which is the specific thing being ruled out. Grounding the card
        # on the film's OWN last frame — blurred past recognition, darkened, tinted toward the
        # brand accent — keeps the natural colours of the world the viewer was just in and gives
        # the logo a clean dark field to sit on. The frame is already extracted for continuity.
        bg = (f"linear-gradient(0deg, rgba(6,6,8,.82), rgba(6,6,8,.82)), "
              f"radial-gradient(120% 96% at 50% 42%, {_rgb_to_hex(_mix(ac, (12, 11, 16), 0.55))}66 0%, "
              f"transparent 62%), url('{ground_uri}') center/cover no-repeat")
        ink, sub = "#F7F2EC", "rgba(247,242,236,0.80)"
        rule = _rgb_to_hex(_mix(ac, (255, 255, 255), 0.28))
        cta_border = "rgba(247,242,236,0.50)"
    elif dark:
        # deep brand-tinted charcoal (keeps the brand hue but dark, so a light/gold logo pops)
        center = _rgb_to_hex(_mix(ac, (12, 11, 16), 0.72))
        mid = _rgb_to_hex(_mix(ac, (8, 8, 11), 0.87))
        bg = f"radial-gradient(120% 96% at 50% 42%, {center} 0%, {mid} 58%, #060608 100%)"
        ink, sub = "#F5EFE9", "rgba(245,239,233,0.78)"
        rule = _rgb_to_hex(_mix(ac, (255, 255, 255), 0.20))
        cta_border = "rgba(245,239,233,0.45)"
    else:
        # brand-tinted ivory (a dark wordmark pops here)
        center = _rgb_to_hex(_mix(ac, (255, 255, 255), 0.86))
        mid = _rgb_to_hex(_mix(ac, (255, 255, 255), 0.93))
        bg = f"radial-gradient(120% 96% at 50% 42%, {center} 0%, {mid} 62%, #F3ECE2 100%)"
        ink, sub = "#2A1A1F", "rgba(42,26,31,0.74)"
        rule = _rgb_to_hex(_mix(ac, (0, 0, 0), 0.12))
        cta_border = _rgb_to_hex(_mix(ac, (0, 0, 0), 0.18))

    head_family = ("'Fraunces','Amiri','Cairo',serif" if elegant
                   else "'Space Grotesk','Cairo',system-ui,sans-serif")
    name_weight = 500 if elegant else 700
    name_ls = "0.05em" if elegant else "0.01em"
    name_size = round(width * (0.058 if elegant else 0.07))

    logo_block = (f'<div class="logo-wrap"><div class="halo"></div>'
                  f'<img class="logo" src="{logo_uri}"></div>' if logo_uri else "")
    # The wordmark ALREADY says the name — repeating it as plain text under the logo reads
    # amateur (owner on the v2 card: «جودة الاند وحشة»). The text name is the NO-LOGO fallback.
    name_block = f'<div class="name">{name}</div>' if (name and not logo_uri) else ""
    # The old chunky white "button" implied a tap in a NON-interactive video (owner: "الزار دا
    # إيه لازمته"). Elegant brands: no fake button — the brand's own URL is the real "where to
    # go", set as a refined letter-spaced line. Non-elegant: a light hairline CTA chip + the URL.
    cta_block = ("" if (elegant or not cta)
                 else f'<div class="cta">{cta}</div>')
    url_block = f'<div class="url">{url_label}</div>' if url_label else ""

    return f"""<!doctype html><html dir="{'rtl' if rtl else 'ltr'}" lang="ar"><head><meta charset="utf-8">
{_FONTS_LINK}
<style>
 html,body{{margin:0;padding:0;width:{width}px;height:{height}px;}}
 .card{{position:relative;width:{width}px;height:{height}px;overflow:hidden;
   font-family:'Space Grotesk','Cairo',system-ui,sans-serif;color:{ink};text-align:center;
   background:{bg};}}
 .grain{{position:absolute;inset:0;opacity:.05;pointer-events:none;
   background:radial-gradient(circle at 50% 42%, rgba(255,255,255,.10) 0%, transparent 42%);}}
 .wrap{{position:absolute;left:0;right:0;top:50%;transform:translateY(-50%);
   display:flex;flex-direction:column;align-items:center;gap:30px;padding:0 96px;box-sizing:border-box;}}
 .logo-wrap{{position:relative;display:flex;align-items:center;justify-content:center;}}
 .halo{{position:absolute;width:170%;height:270%;left:-35%;top:-85%;z-index:0;pointer-events:none;
   background:radial-gradient(circle at 50% 50%, {accent}5c 0%, {accent}26 34%, transparent 66%);
   filter:blur(34px);}}
 .logo{{position:relative;z-index:1;height:{round(height*0.19)}px;max-width:78%;object-fit:contain;
   filter:drop-shadow(0 8px 26px rgba(0,0,0,{'0.55' if dark else '0.16'}));}}
 .rule{{width:56px;height:1px;background:{rule};opacity:.85;}}
 .name{{font-family:{head_family};font-weight:{name_weight};font-size:{name_size}px;
   letter-spacing:{name_ls};line-height:1.16;max-width:82%;}}
 .cta{{font-family:'Space Grotesk','Cairo',sans-serif;font-weight:600;
   font-size:{round(width*0.035)}px;letter-spacing:0.14em;text-transform:uppercase;color:{ink};
   border:1px solid {cta_border};border-radius:999px;padding:0.7em 1.7em;}}
 .url{{font-family:'Space Grotesk','Cairo',sans-serif;font-weight:700;font-size:{round(width*0.031)}px;
   letter-spacing:0.2em;text-transform:uppercase;color:{sub};}}
</style></head>
<body><div class="card"><div class="grain"></div>
 <div class="wrap">{logo_block}<div class="rule"></div>{name_block}{cta_block}{url_block}</div>
</div></body></html>"""


def _url_label(profile: dict) -> str:
    """A clean domain for the end-card 'where to go' line (eg.azzafahmy.com -> azzafahmy.com)."""
    from urllib.parse import urlparse
    for k in ("final_url", "source_url"):
        v = profile.get(k)
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, str) and v.strip():
            host = urlparse(v if "//" in v else "http://" + v).netloc.split("@")[-1].split(":")[0].lower()
            parts = [p for p in host.split(".") if p]
            # drop leading www / common country-shop subdomains so the brand domain reads clean
            while len(parts) > 2 and parts[0] in ("www", "eg", "m", "shop", "store", "en", "ar"):
                parts = parts[1:]
            return ".".join(parts)
    return ""


def render_endcard(profile: dict, out_png: Path, *, width: int = REEL_W, height: int = REEL_H,
                   ground_frame: "str | Path | None" = None) -> Optional[Path]:
    """Render the brand end-card to a PNG via Chromium (Arabic shapes correctly). None on failure."""
    try:
        from playwright.sync_api import sync_playwright

        from reel.from_profile import build_reel_brief, is_rtl
        from reel.text_overlay import _appropriate_cta, _reel_theme
        from reel.textlayer import _logo_data_uri

        brief = build_reel_brief(profile)
        name = (brief.business_name or "").strip()
        cta = _appropriate_cta((brief.cta_text or "").strip() or ("اعرف أكتر" if is_rtl(name) else "Learn more"),
                               profile, is_rtl(name))
        pal = [c for c in (brief.palette_hex or []) if isinstance(c, str) and c.startswith("#")]
        accent = pal[0] if pal else "#1b2340"
        accent2 = pal[1] if len(pal) > 1 else accent
        logo_uri = _logo_data_uri(brief.logo_url) if brief.logo_url else None
        # Theme (elegant serif for luxury/fashion, same signal as the captions) + a contrast-aware
        # background so the logo is never lost against a same-hue brand colour (the gold-on-gold
        # end-card the owner flagged). A light/gold/white logo -> dark card; a dark logo -> light.
        elegant = _reel_theme(profile) == "elegant"
        dark = _logo_mean_luma(brief.logo_url) >= 130.0
        # The film's own last frame, blurred and darkened, as the card's ground.
        ground_uri = _ground_data_uri(ground_frame, width, height)
        html = _endcard_html(logo_uri, name, cta, _url_label(profile), accent, accent2,
                             is_rtl(name), width, height, elegant=elegant, dark=dark,
                             ground_uri=ground_uri)

        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                page.set_content(html, wait_until="networkidle")
                try:
                    page.evaluate("async () => { await document.fonts.ready; }")
                except Exception:
                    pass
                page.screenshot(path=str(out_png), clip={"x": 0, "y": 0, "width": width, "height": height})
            finally:
                browser.close()
        # P-38: trim any near-black letterbox band off the rendered card (a no-op on our full-canvas
        # HTML, but the safety net the frame inspection asked for). Reuses the reel image trimmer.
        if out_png.exists():
            try:
                from reel.image_quality import trim_letterbox_png
                trim_letterbox_png(str(out_png))
            except Exception:
                pass
        return out_png if out_png.exists() else None
    except Exception:
        return None


def _video_dims(path: "str | Path") -> "tuple[int, int] | None":
    """(width, height) from ffmpeg's stderr banner. None on failure — the caller must then
    say so, not guess."""
    import re
    import subprocess

    from reel.ffmpeg_tools import ffmpeg_exe
    try:
        r = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
        m = re.search(r"Video:.*?\s(\d{2,5})x(\d{2,5})", r.stderr or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:  # noqa: BLE001
        pass
    return None


def append_endcard_to_reel(profile: dict, video_path: str | Path, *,
                           seconds: float = ENDCARD_S, fps: int = 30,
                           width: int = 0, height: int = 0,
                           tail_audio: "str | Path | None" = None) -> bool:
    """Append a ~`seconds` brand end-card (gentle push-in + fade) to the reel, IN PLACE, with a
    short cross-fade from the last scene. Returns False (reel untouched) on failure — the
    CALLER stamps that outcome; nothing here may pretend it appended.

    The card is rendered at the REEL'S OWN probed dimensions, never a fixed canvas: the v2 Abu
    Auf reel measured 772x1372 while the card assumed 768x1366, xfade refused the mismatch, and
    the reel shipped card-less behind a printed [ok].

    `tail_audio` is the sound bed the film was cut to, written long enough to cover this card.
    Without it the card gets `anullsrc` and the reel's audio cross-fades INTO SILENCE — which is
    why every delivered reel went quiet before its last frame (MEASURED: alfouad_run3 silent from
    12.73s of 15.30s, nanocare_film from 10.40s of 16.69s). A commercial does the opposite: the
    track lands ON the end frame. Defaults to `<reel>.bed.m4a`, which is exactly where
    `reel/creative.py` writes it, so the common path needs no plumbing; still silent when there is
    no bed, because inventing audio for a card is worse than a quiet one."""
    try:
        from reel.motion import _clip_duration, _has_audio

        video_path = Path(video_path)
        if not video_path.is_file():
            return False
        dims = _video_dims(video_path)
        if dims:
            # libx264 needs EVEN dimensions; an odd-width reel would fail the card encode
            # and silently cost the card again.
            width, height = (dims[0] - dims[0] % 2, dims[1] - dims[1] % 2)
        elif not (width and height):
            logger.warning("endcard: could not probe the reel's dimensions — not appending")
            return False
        work = video_path.parent
        # THE CARD ENDS THE FILM IT IS ATTACHED TO. This function already holds the finished reel,
        # so the frame the viewer is looking at one instant before the card appears is free to
        # take — the same extractor the continuity chain uses. `None` on any failure, and the card
        # falls back to its synthetic gradient: a decoration must never cost the card itself.
        try:
            from reel.continuity import tail_frame
            ground = tail_frame(video_path, work / "_endcard_ground.png")
        except Exception:  # noqa: BLE001
            ground = None
        png = render_endcard(profile, work / "_endcard.png", width=width, height=height,
                             ground_frame=ground)
        if png is None:
            return False

        # end-card -> a clip: slow push-in. NO SECOND DISSOLVE.
        #
        # This carried `fade=t=in:st=0:d=0.4` on top of the 0.5s xfade below, so the logo arrived
        # through TWO overlapping dissolves — 0.9s of the card's 1.8s spent not yet being visible.
        # The mark smeared into view instead of landing, which is the opposite of what an end frame
        # is for: the last thing a viewer sees should arrive on a beat and hold. The xfade IS the
        # join; a card that also fades in is fading in twice.
        card = work / "_endcard.mp4"
        frames = int(seconds * fps)
        vf = (f"scale={width}:{height},zoompan=z='1.0+0.05*on/{frames}':d={frames}:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},"
              f"format=yuv420p")
        run_ffmpeg(["-loop", "1", "-i", str(png), "-t", f"{seconds:.2f}", "-vf", vf, "-r", str(fps),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20", str(card)])

        # xfade the reel -> the end-card; the reel's AUDIO (Veo voiceover + ambience) must
        # SURVIVE the append — the silent card gets a padded track and the sound cross-fades
        # to silence over it (mapping only [v] here used to drop the whole voiceover).
        t = ENDCARD_XFADE_S
        main_dur = _clip_duration(video_path)
        off = max(0.0, main_dur - t)
        out = work / "_with_endcard.mp4"
        graph = (f"[0:v]settb=AVTB,fps={fps}[va];[1:v]settb=AVTB,fps={fps}[vb];"
                 f"[va][vb]xfade=transition=fade:duration={t}:offset={off:.2f}[v]")
        args = ["-i", str(video_path), "-i", str(card)]
        maps = ["-map", "[v]"]
        if _has_audio(video_path):
            # THE CARD'S AUDIO IS THE FILM'S LAST BAR, not silence. The bed was synthesized to
            # cover `main_dur + seconds - t`, so its final `seconds` are exactly what belongs
            # under the card; seeking to that offset keeps the music continuous across the join
            # instead of cross-fading a track into nothing.
            bed = Path(tail_audio) if tail_audio else video_path.with_suffix(".bed.m4a")
            bed_dur = _clip_duration(bed) if bed.is_file() else 0.0
            if bed.is_file() and bed_dur > main_dur + 0.05:
                args += ["-ss", f"{max(0.0, bed_dur - seconds):.3f}", "-t", f"{seconds:.2f}",
                         "-i", str(bed)]
            else:
                args += ["-f", "lavfi", "-t", f"{seconds:.2f}",
                         "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            # apad the reel's audio to EXACTLY the probed video length first: if the
            # AAC track runs a few ms short, acrossfade starves at the join and the
            # voiceover cuts abruptly instead of fading into the card.
            # The fade lives in the GRAPH, not in an `-af` before an input — `-af` is an output
            # option and would be applied to the wrong stream (or rejected). It runs on either
            # branch: fading silence is a no-op, and one path is easier to trust than two.
            graph += (f";[0:a]apad=whole_dur={main_dur:.3f}[a0]"
                      f";[2:a]afade=t=out:st={max(0.0, seconds - 0.6):.2f}:d=0.6[a2]"
                      f";[a0][a2]acrossfade=d={t}[aout]")
            maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        run_ffmpeg(args + ["-filter_complex", graph] + maps
                   + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
                      "-profile:v", "high", "-preset", "medium", "-crf", "20", str(out)])
        import shutil
        shutil.move(str(out), str(video_path))
        for f in (png, card):
            try:
                Path(f).unlink()
            except OSError:
                pass
        return True
    except Exception as exc:  # noqa: BLE001
        # LOUD (v2 lesson): the 0-byte _with_endcard.mp4 sat on disk while [ok] printed.
        logger.warning("endcard append FAILED (%s: %s) — the reel ships without its card",
                       type(exc).__name__, str(exc)[:200])
        return False


def _ground_data_uri(frame: "str | Path | None", width: int, height: int) -> str:
    """The reel's last frame as a blurred, darkened data URI for the end card's ground.

    Blurred PAST RECOGNITION on purpose. The point is not to show the scene again — the viewer
    just watched it — but to end on the film's own COLOUR and light instead of on a generated
    card. Anything still legible would read as a freeze-frame the edit forgot to cut.

    Returns "" for anything unusable, and the caller falls back to the synthetic gradient. An end
    card must never fail to exist because a decoration could not be made."""
    import base64
    import io
    if not frame:
        return ""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        p = Path(str(frame))
        if not p.is_file():
            return ""
        im = Image.open(p).convert("RGB")
        # Cover-fit to the card, then blur relative to the SHORT side so the effect is the same
        # whatever resolution the reel rendered at.
        scale = max(width / im.width, height / im.height)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                       Image.LANCZOS)
        left, top = (im.width - width) // 2, (im.height - height) // 2
        im = im.crop((left, top, left + width, top + height))
        im = im.filter(ImageFilter.GaussianBlur(radius=max(18, min(width, height) * 0.055)))
        im = ImageEnhance.Brightness(im).enhance(0.55)
        im = ImageEnhance.Color(im).enhance(0.85)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return ""
