"""HTML/CSS -> PNG via headless Chromium (Playwright). No Pillow.

Renders the full poster (background + overlaid text zones) by loading an HTML
string in Chromium and screenshotting a fixed poster canvas. device_scale_factor
gives crisp typography (2x => 2160x2700 pixels for a 1080x1350 CSS canvas).
"""
from __future__ import annotations

from pathlib import Path

POSTER_W = 1080
POSTER_H = 1350


def render_html_to_png(
    html: str,
    out_path: str | Path,
    *,
    width: int = POSTER_W,
    height: int = POSTER_H,
    scale: int = 2,
    offline: bool = False,
) -> Path:
    from playwright.sync_api import sync_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=scale,
            )
            # Background is inlined as a data URI, but Google Fonts load over the
            # network — networkidle covers their requests, and document.fonts.ready
            # guarantees the faces are applied before we screenshot (else the first
            # paint can fall back to a system font).
            if offline: page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="networkidle")
            try:
                page.evaluate("async () => { await document.fonts.ready; }")
            except Exception:
                pass
            page.screenshot(
                path=str(out_path),
                clip={"x": 0, "y": 0, "width": width, "height": height},
            )
        finally:
            browser.close()

    return out_path


def inspect_layout(html: str, *, offline: bool = False) -> dict:
    """Local compositor geometry/ink measurements; not a vision verdict."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':POSTER_W,'height':POSTER_H})
            if offline: page.route('**/*', lambda route: route.abort())
            page.set_content(html,wait_until='networkidle')
            page.evaluate('document.fonts.ready')
            boxes=page.evaluate('''() => ['.brand-logo','.headline,.hero-headline,.lockup-headline','.sub','.cta'].flatMap(s=>{
                const e=document.querySelector(s);if(!e)return [];const r=e.getBoundingClientRect(),c=getComputedStyle(e.querySelector('.cta-text')||e);
                return [{kind:s,x:r.x,y:r.y,right:r.right,bottom:r.bottom,font:parseFloat(c.fontSize),direction:c.direction,overflow:e.scrollWidth>e.clientWidth+1}];})''')
        finally: browser.close()
    defects=[]
    for b in boxes:
        if b['x']<24 or b['y']<24 or b['right']>1056 or b['bottom']>1326 or b['overflow']:
            defects.append('LOCAL_CLIPPING')
        if b['kind']=='.sub' and b['font']<44: defects.append('LOCAL_SMALL_PROOF')
        if b['kind']=='.cta' and b['font']<38: defects.append('LOCAL_SMALL_CTA')
    for i,a in enumerate(boxes):
        for b in boxes[i+1:]:
            if min(a['right'],b['right'])>max(a['x'],b['x']) and min(a['bottom'],b['bottom'])>max(a['y'],b['y']):
                defects.append('LOCAL_OVERLAP')
    return {'source':'LOCAL_COMPOSITOR_MEASUREMENT','policy_version':1,'boxes':boxes,'defects':sorted(set(defects))}
