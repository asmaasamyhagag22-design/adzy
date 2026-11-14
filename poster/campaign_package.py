"""Offline review exports for the evidence-bound campaign workflow."""
from base64 import b64encode
from functools import lru_cache
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from poster.campaign_catalog import CATALOG_CSS, INGREDIENT_CSS, INGREDIENT_DRAWINGS, INGREDIENT_LAYOUTS, catalog_cards
from poster.campaign_scene import measure_scene, scene_html
from poster.render_playwright import render_html_to_png
from strategy.campaign_artifacts import MAX_CAMPAIGN_ARCHIVE_BYTES, PosterHashes, PreparedCampaignBundle
from strategy.campaign_package import digest


SIZES = {"portrait": (1080, 1350), "square": (1080, 1080)}
DRAFT_CACHE_VERSION = "preview-v2"
THEMES = {
    "ember": ("#321517", "#fff4dc", "#ffbb5e", "#8d3834", "#fc8057"),
    "cobalt": ("#130d36", "#fff7ef", "#ffc983", "#5829a5", "#bc91ff"),
    "forest": ("#112d28", "#fff9e9", "#e7ed86", "#216655", "#78c8a2"),
}


class PosterLayoutError(ValueError):
    pass


@lru_cache(maxsize=4)
def font_data(name):
    return b64encode((Path(__file__).parent / "fonts" / name).read_bytes()).decode()


def poster_html(package, width, height):
    bg, ink, accent, shape, light = THEMES[package["theme"]]
    regular = font_data("NotoSansArabic-Regular.ttf")
    bold = font_data("NotoSansArabic-Bold.ttf")
    latin_regular = font_data("NotoSans-Regular.ttf")
    latin_bold = font_data("NotoSans-Bold.ttf")
    text = {key: escape(value) for key, value in package["copy"].items()}
    brand = escape(package["brand_name"])
    lang, direction = escape(package["language"]), escape(package["direction"])
    concept = package.get("concept") if package["poster_layout"] == "source-campaign@2" else None
    art = {
        "compare": '<div class="frames"><span></span><span></span></div>',
        "assemble": '<div class="tiles"><span></span><span></span><span></span><span></span></div>',
        "filter": '<div class="filter"><span></span><span></span><span></span><b></b></div>',
    }.get(concept, '<i></i><i></i><i></i>')
    ingredients = package["poster_layout"] == "source-campaign@4"
    catalog = ingredients or package["poster_layout"] == "source-campaign@3"
    layout = escape(package["art"]["layout"]) if ingredients else ""
    if catalog:
        drawing_label = "رسم توضيحي" if package["language"].startswith("ar") else "Illustration"
        content = f"""<header class="brand" dir="auto">{brand}</header>
 <h1>{text["headline"]}</h1>
 <div class="motif">{catalog_cards(package["art"])}</div>
 <section class="copy"><p class="illustration-note">{drawing_label}</p>
 <div class="catalog-action"><p class="message">{text["message"]}</p><p class="cta">{text["cta"]}</p></div></section>"""
    else:
        content = f"""<header class="brand" dir="auto">{brand}</header>
 <div class="motif" aria-hidden="true">{art}</div>
 <section class="copy"><h1>{text["headline"]}</h1><p class="message">{text["message"]}</p>
 <p class="cta">{text["cta"]}</p></section>"""
    return f"""<!doctype html>
<html lang="{lang}" dir="{direction}">
<meta charset="utf-8">
<style>
@font-face{{font-family:Campaign;src:url(data:font/ttf;base64,{regular});font-weight:400}}
@font-face{{font-family:Campaign;src:url(data:font/ttf;base64,{bold});font-weight:700}}
@font-face{{font-family:CampaignLatin;src:url(data:font/ttf;base64,{latin_regular});font-weight:400}}
@font-face{{font-family:CampaignLatin;src:url(data:font/ttf;base64,{latin_bold});font-weight:700}}
*{{box-sizing:border-box}}
body{{margin:0;color:{ink};background:{bg};font-family:Campaign,CampaignLatin,sans-serif}}
main{{width:{width}px;height:{height}px;padding:64px 76px;display:grid;
 grid-template-rows:auto minmax(140px,1fr) auto;gap:30px;overflow:hidden;
 background:radial-gradient(ellipse at 30% 25%,{shape} 0,transparent 64%),{bg};}}
.brand{{border-block-end:2px solid {accent};padding-block-end:16px;
 font-size:40px;font-weight:700;line-height:1.35;overflow-wrap:anywhere}}
.motif{{position:relative;isolation:isolate;overflow:hidden;min-height:140px;
 background-image:radial-gradient({light}45 1.5px,transparent 2px);
 background-size:30px 30px;mask-image:linear-gradient(90deg,transparent,#000 24%,#000 76%,transparent)}}
.motif:has(.frames){{container-type:size}}
.motif > i{{position:absolute;inset-block-start:50%;inset-inline-start:50%;
 width:42%;height:82%;border-radius:50%;border:5px solid {light};translate:-50% -50%;
 transform:rotate(-28deg);background:linear-gradient(150deg,{light}55,{shape}44);
 box-shadow:0 0 70px {light}33,inset 0 0 70px {light}55}}
[dir=rtl] .motif > i{{translate:50% -50%}}
.motif > i:nth-child(2){{transform:rotate(28deg);width:58%;height:59%;border-color:{accent}}}
.motif > i:nth-child(3){{transform:rotate(70deg);width:32%;height:93%;border-color:{ink}99}}
main[data-theme=ember] .motif > i{{height:34%;width:59%;border-radius:46%;
 transform:translateY(-68%) rotate(-7deg);background:linear-gradient(172deg,{accent}aa,{shape}33)}}
main[data-theme=ember] .motif > i:nth-child(2){{transform:rotate(7deg);width:74%}}
main[data-theme=ember] .motif > i:nth-child(3){{transform:translateY(68%) rotate(-7deg);width:55%}}
main[data-theme=forest] .motif > i{{border-radius:16%;width:24%;height:64%;
 transform:translateX(52%) rotate(-24deg);background:linear-gradient(40deg,{accent}77,{shape})}}
main[data-theme=forest] .motif > i:nth-child(2){{transform:rotate(12deg)}}
main[data-theme=forest] .motif > i:nth-child(3){{transform:translateX(-51%) rotate(-9deg)}}
.frames,.tiles,.filter{{position:absolute;inset:8% 7%;display:grid;align-content:center;justify-content:center}}
.frames{{grid-template-columns:repeat(2,minmax(120px,1fr));gap:7%;direction:ltr}}
.frames span{{height:min(72cqh,375px);border:7px solid {accent};outline:2px solid {light};
 outline-offset:10px;border-radius:8px;background:linear-gradient(155deg,{shape} 0 43%,{light} 43% 46%,{bg} 46% 100%)}}
.frames span+span{{background:radial-gradient(circle at 44% 42%,{accent} 0 11%,{light} 11% 18%,transparent 18%),linear-gradient(28deg,{shape} 0 50%,{bg} 50%)}}
.tiles{{grid-template-columns:repeat(2,clamp(90px,15vh,190px));gap:18px;direction:ltr}}
.tiles span{{aspect-ratio:1;border-radius:20px;background:{shape};border:4px solid {light};
 box-shadow:inset 0 0 0 16px {bg}44}}
.tiles span:nth-child(2){{border:4px dashed {accent};background:transparent;transform:translate(18px,-14px)}}
.tiles span:nth-child(4){{background:{accent};border-color:{accent}}}
.filter{{inset:10% 10%;grid-template-rows:repeat(3,8px);row-gap:42px;justify-content:stretch;direction:ltr}}
.filter span{{width:100%;border-radius:8px;background:linear-gradient(90deg,{light} 0 49%,transparent 49% 56%,{light}55 56% 100%)}}
.filter span:nth-child(2){{background:linear-gradient(90deg,{accent} 0 49%,transparent 49% 100%)}}
.filter span:nth-child(3){{background:linear-gradient(90deg,{light} 0 49%,transparent 49% 100%)}}
.filter b{{position:absolute;left:50%;inset-block:13%;width:7px;border-radius:9px;background:{accent}}}
.copy{{display:grid;gap:18px;min-width:0}}
h1,p{{margin:0;overflow-wrap:anywhere}}
h1{{font-weight:700;font-size:88px;line-height:1.26;letter-spacing:-.012em;text-wrap:balance}}
.message{{font-size:44px;line-height:1.46}}
.cta{{justify-self:start;padding:12px 26px;border-radius:12px;background:{accent};color:{bg};
 font-size:37px;line-height:1.45;font-weight:700}}
{CATALOG_CSS if catalog else ""}
{INGREDIENT_CSS if ingredients else ""}
</style>
<main data-theme="{escape(package["theme"])}" data-layout="{layout}" class="{'catalog ingredients' if ingredients else 'catalog' if catalog else ''}">{content}</main>
</html>"""


def measure_poster(page):
    boxes = page.locator(".brand, .motif, h1, .message, .cta").evaluate_all(
        """elements => elements.map(element => {
            const r = element.getBoundingClientRect();
            const css = getComputedStyle(element);
            return {field: element.className || element.tagName, x: r.x, y: r.y,
                right: r.right, bottom: r.bottom, size: parseFloat(css.fontSize),
                color: css.color, background: css.backgroundColor,
                overflow: element.scrollWidth > element.clientWidth + 1 ||
                    Array.from(element.querySelectorAll('.frames span, .catalog-item')).some(frame => {
                        const f = frame.getBoundingClientRect();
                        const outline = frame.matches('.frames span') ? parseFloat(getComputedStyle(frame).outlineOffset) + 2 : 0;
                        return f.top - outline < r.top || f.bottom + outline > r.bottom ||
                            (outline === 0 && (f.left < r.left || f.right > r.right ||
                            frame.scrollHeight > frame.clientHeight + 1 || frame.scrollWidth > frame.clientWidth + 1 ||
                            (() => {
                                const drawing = frame.querySelector('.food-drawing').getBoundingClientRect();
                                const name = frame.querySelector('.item-name').getBoundingClientRect();
                                const details = frame.querySelector('.item-detail').getBoundingClientRect();
                                const intersects = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
                                    Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
                                return drawing.top < f.top || intersects(drawing, name) || intersects(drawing, details) ||
                                    drawing.left < f.left || drawing.right > f.right || drawing.bottom > f.bottom ||
                                    name.bottom > details.top + 1 ||
                                    Array.from(frame.querySelectorAll('.food-drawing i')).some(layer => {
                                        const b = layer.getBoundingClientRect();
                                        return b.top < drawing.top || b.bottom > drawing.bottom ||
                                            b.left < drawing.left || b.right > drawing.right;
                                    });
                            })()));
                    })};
        })"""
    )
    return boxes


def layout_poster(page, width, height):
    boxes = measure_poster(page)
    for headline, message, cta in ((76, 42, 36), (67, 40, 35), (60, 39, 34), (56, 37, 32)):
        if max(box["bottom"] for box in boxes) <= height - 60:
            break
        page.add_style_tag(content=f"h1{{font-size:{headline}px}}.message{{font-size:{message}px}}.cta{{font-size:{cta}px}}")
        boxes = measure_poster(page)
    if any(box["x"] < 63 or box["right"] > width - 63 or box["y"] < 60 or
           box["bottom"] > height - 60 or box["overflow"] for box in boxes):
        raise PosterLayoutError("POSTER_CLIPPING")
    for index, a in enumerate(boxes):
        for b in boxes[index + 1:]:
            if min(a["bottom"], b["bottom"]) - max(a["y"], b["y"]) > 1 and (
                min(a["right"], b["right"]) - max(a["x"], b["x"]) > 1
            ):
                raise PosterLayoutError("POSTER_OVERLAP")
    return boxes


def render_poster(package, label, path):
    scene = package["poster_layout"] == "source-campaign@5"
    if not scene and package["poster_layout"] not in {
        "source-campaign@1", "source-campaign@2", "source-campaign@3", "source-campaign@4",
    }:
        raise PosterLayoutError("POSTER_LAYOUT_UNAVAILABLE")
    if package["poster_layout"] == "source-campaign@4":
        art = package["art"]
        if art.get("layout") not in INGREDIENT_LAYOUTS or len(art["subjects"]) != 2 or any(
            item.get("illustration") not in INGREDIENT_DRAWINGS for item in art["subjects"]
        ):
            raise PosterLayoutError("POSTER_LAYOUT_UNAVAILABLE")
    width, height = SIZES[label]
    geometry = []
    runtime = {}
    try:
        if scene:
            fonts = tuple(font_data(name) for name in (
                "NotoSansArabic-Regular.ttf", "NotoSansArabic-Bold.ttf",
                "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
            ))
            html = scene_html(package, width, height, fonts)
        else:
            html = poster_html(package, width, height)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise PosterLayoutError("POSTER_SCENE_UNAVAILABLE") from exc
    validate = measure_scene if scene else layout_poster

    def capture_layout(page):
        geometry.extend(validate(page, width, height))
        runtime["chromium"] = page.context.browser.version

    try:
        render_html_to_png(html, path, width=width, height=height, scale=1, offline=True,
                           validate=capture_layout)
    except ValueError as exc:
        raise PosterLayoutError(str(exc)) from exc
    image = path.read_bytes()
    return image, {"width": width, "height": height, "sha256": sha256(image).hexdigest(),
                   "geometry": geometry, "renderer": {**runtime, "html_sha256": sha256(html.encode()).hexdigest()}}


def read_poster(path):
    try:
        with ZipFile(path) as asset:
            if (asset.getinfo("poster.png").file_size > MAX_CAMPAIGN_ARCHIVE_BYTES or
                    asset.getinfo("measurements.json").file_size > 1024 * 1024):
                raise ValueError("Poster archive exceeds its size limit")
            image = asset.read("poster.png")
            measurements = json.loads(asset.read("measurements.json"))
        if sha256(image).hexdigest() != measurements["sha256"]:
            raise ValueError("Poster checksum mismatch")
    except (BadZipFile, ValueError, KeyError, TypeError) as exc:
        raise PosterLayoutError("POSTER_ARTIFACT_CORRUPT") from exc
    return image, measurements


def poster_path(view, size, root):
    result = view["proposal"]
    return root / DRAFT_CACHE_VERSION / view["run_id"] / result["sha256"] / f"{size}.zip"


def stored_poster(view, size, root):
    path = poster_path(view, size, root)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        return read_poster(path)
    except FileNotFoundError:
        pass
    with TemporaryDirectory(prefix="render-", dir=directory) as work:
        work = Path(work)
        image, measurements = render_poster(view["proposal"]["package"], size, work / "poster.png")
        candidate = work / "asset.zip"
        with ZipFile(candidate, "w") as asset:
            asset.writestr("poster.png", image)
            asset.writestr("measurements.json", json.dumps(measurements, sort_keys=True))
        # Publish a complete artifact once, even when independent processes render the same run.
        try:
            os.link(candidate, path)
        except FileExistsError:
            return read_poster(path)
    return image, measurements


def preview_poster(view, size, root):
    image, _ = stored_poster(view, size, root)
    return image


def prepare_approval(view, root, expected: PosterHashes) -> PreparedCampaignBundle:
    assets = {}
    try:
        for label in SIZES:
            assets[label] = read_poster(poster_path(view, label, root))
    except FileNotFoundError as exc:
        raise PosterLayoutError("POSTER_PREVIEWS_REQUIRED") from exc
    poster_hashes = {label: report["sha256"] for label, (_, report) in assets.items()}
    if poster_hashes != expected:
        raise PosterLayoutError("POSTER_PREVIEW_CHANGED")
    if sum(len(image) for image, _ in assets.values()) >= MAX_CAMPAIGN_ARCHIVE_BYTES:
        raise PosterLayoutError("CAMPAIGN_ARCHIVE_TOO_LARGE")
    manifest = {
        "version": 1,
        "proposal_sha256": view["proposal"]["sha256"],
        "poster_sha256": poster_hashes,
        "measurements_sha256": {label: digest(report) for label, (_, report) in assets.items()},
    }
    approval_id = uuid4()
    result = view["proposal"]
    archive = BytesIO()
    with ZipFile(archive, "w") as zip_file:
        for label, (image, _) in assets.items():
            zip_file.writestr(ZipInfo(f"poster-{label}.png"), image)
        zip_file.writestr(ZipInfo("campaign.json"), json.dumps(
            {"run_id": view["run_id"], "approval_id": str(approval_id),
             "profile_snapshot_id": view["profile_snapshot_id"], "proposal": result,
             "poster_measurements": {label: report for label, (_, report) in assets.items()},
             "approved_artifacts": manifest, "review_status": "LOCAL_REVIEW",
             **({"video": "SCRIPT_AND_STORYBOARD_ONLY"} if result["package"].get("storyboard") else {})},
            indent=2, ensure_ascii=False, sort_keys=True,
        ), compress_type=ZIP_DEFLATED)
    data = archive.getvalue()
    if len(data) > MAX_CAMPAIGN_ARCHIVE_BYTES:
        raise PosterLayoutError("CAMPAIGN_ARCHIVE_TOO_LARGE")
    return PreparedCampaignBundle(
        run_id=UUID(view["run_id"]), profile_snapshot_id=UUID(view["profile_snapshot_id"]),
        previous_approval_id=view["approval_id"], approval_id=approval_id,
        manifest={**manifest, "archive_sha256": sha256(data).hexdigest()}, archive=data,
    )


def approved_preview(archive, size):
    try:
        with ZipFile(BytesIO(archive)) as bundle:
            return bundle.read(f"poster-{size}.png")
    except (BadZipFile, KeyError) as exc:
        raise PosterLayoutError("APPROVED_ARTIFACT_CORRUPT") from exc
