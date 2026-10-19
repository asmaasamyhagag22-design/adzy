"""Offline case art direction through the production HTML compositor; no approval or provider work."""
import argparse
import base64
import hashlib
import html as html_lib
import json
import re
from pathlib import Path
from poster.schemas import PosterBrief, PosterDesignSpec
from poster.template import render_poster_html
from poster.render_playwright import render_html_to_png

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'artifacts/adzy_visual_revision_20261018'


def uri(path, mime):
    return f'data:{mime};base64,' + base64.b64encode(Path(path).read_bytes()).decode()


def local_html(html, directory):
    html = re.sub(r'<link[^>]+(?:fonts.googleapis|fonts.gstatic)[^>]*>', '', html)
    css = ''
    for weight in ('Regular', 'Bold'):
        font = directory/'assets'/f'NotoSansArabic-{weight}.ttf'
        if not font.exists():
            font.write_bytes(Path('/usr/share/fonts/truetype/noto', font.name).read_bytes())
        css += f"@font-face{{font-family:RevisionArabic;src:url({uri(font,'font/ttf')});font-weight:{400 if weight=='Regular' else 700};}}"
    return html.replace('</head>', f'<style>{css}.headline,.sub,.cta-text{{font-family:RevisionArabic,sans-serif!important;}}</style></head>')


def prepare(directory=DEFAULT):
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    assets = directory/'assets'; assets.mkdir(exist_ok=True)
    for name in ('logo.svg', 'artwork.svg'):
        target=assets/name
        source=ROOT/'docs/demo/firefox'/name
        if not target.exists(): target.write_bytes(source.read_bytes())
        assert target.read_bytes()==source.read_bytes(), 'Public source asset changed'
    brief=PosterBrief(business_name='Firefox', headline='الخصوصية مُدمجة',
        subheadline='فنحن نحظر مُعظم المتعقّبات تلقائيًا.', cta_text='نزِّل Firefox',
        cta_url='https://www.firefox.com/ar/', palette_hex=['#20123a','#ff7139','#f8f6f4'],
        primary_color='#20123a', logo_url=uri(assets/'logo.svg','image/svg+xml'))
    # Each direction has its own preserved art inputs; none enter canonical operations.
    (directory/'brief.json').write_text(brief.model_dump_json(indent=2))
    warm='''<html><body style="margin:0;width:1080px;height:1350px;overflow:hidden;background:#f8f6f4">
    <div style="position:absolute;left:-100px;top:650px;width:1280px;height:900px;border-radius:50%;background:#ffb38b"></div>
    <img src="ARTWORK" style="position:absolute;left:318px;top:650px;width:460px;height:585px;object-fit:contain"></body></html>'''.replace('ARTWORK',uri(assets/'artwork.svg','image/svg+xml'))
    # Abstract shelter, not a product interface, measurement, badge or absolute guarantee.
    dark='''<html><body style="margin:0;width:1080px;height:1350px;overflow:hidden;background:#20123a">
    <div style="height:160px;background:#f8f6f4"></div>
    <svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" style="position:absolute;inset:0" viewBox="0 0 1080 1350">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ffbd4f"/><stop offset=".5" stop-color="#ff7139"/><stop offset="1" stop-color="#b251ed"/></linearGradient></defs>
    <g fill="none"><ellipse cx="540" cy="870" rx="420" ry="210" stroke="#8061a6" stroke-opacity=".26"/>
    <ellipse cx="540" cy="870" rx="350" ry="172" stroke="#8061a6" stroke-opacity=".4"/>
    <path d="M540 650 Q650 710 760 707 V840 Q752 1000 540 1080 Q328 1000 320 840 V707 Q430 710 540 650Z" stroke="url(#g)" stroke-width="34" fill="#2c194d"/>
    <path d="M540 718 Q615 756 686 757 V844 Q677 952 540 1008 Q403 952 394 844 V757 Q465 756 540 718Z" stroke="#ffb763" stroke-opacity=".4" stroke-width="2"/>
    <path d="M118 785 Q245 765 302 830 M962 940 Q825 968 775 906" stroke="#bba8d0" stroke-width="3" stroke-dasharray="5 13"/>
    </g><g fill="#ffb763"><circle cx="214" cy="956" r="8"/><circle cx="852" cy="771" r="6"/><circle cx="903" cy="824" r="4"/></g>
    </svg></body></html>'''
    cases={
      'a-official-illustration':(warm, '#20123a', dict(text_box=[.085,.17,.83],logo_xy=[.68,.043],headline_font_size=92,support_font_size=40,cta_font_size=36),
        '.headline{margin-bottom:20px!important;line-height:1.24!important}.sub{margin-bottom:25px!important;max-width:890px}.cta{box-shadow:none!important}.brand-logo{max-width:245px!important}'),
      'b-privacy-shelter':(dark,'#f8f6f4',dict(text_box=[.085,.16,.83],logo_xy=[.68,.043],headline_font_size=116,support_font_size=42,cta_font_size=38),
        '.headline{max-width:880px;line-height:1.17!important;margin-bottom:24px!important;text-shadow:none!important}.sub{max-width:890px;margin-bottom:0!important}.cta{margin-top:540px!important;box-shadow:none!important;background:#ffb763!important;color:#20123a!important;padding:18px 40px!important}.brand-logo{max-width:245px!important}')}
    for name,(bg,ink,values,css) in cases.items():
        case=directory/name;case.mkdir(exist_ok=True)
        spec=PosterDesignSpec(layout='top_anchor',text_align='right',headline_treatment='block',
            accent_word='none',cta_style='block',scrim_strength=0,negative_space_zone='top',
            show=['logo','headline','sub','cta'],accent_hex='#20123a',**values)
        (case/'background.html').write_text(bg)
        background=render_html_to_png(bg,case/'background.png',scale=1)
        (case/'spec.json').write_text(spec.model_dump_json(indent=2))
        (case/'direction.css').write_text(css)
        draw(directory,case,brief,spec,background,css,ink)
    return directory


def draw(directory,case,brief,spec,background,css,ink,emphasis_word=None):
    html=render_poster_html(brief,str(background),spec=spec,copy_ink=ink)
    if emphasis_word:
        escaped=html_lib.escape(brief.headline)
        emphasis=html_lib.escape(emphasis_word)
        assert escaped.count(emphasis)==1
        html=html.replace('>'+escaped+'</h1>', '>'+escaped.replace(emphasis,'<span class="emphasis">'+emphasis+'</span>')+'</h1>')
    html=local_html(html,directory).replace('</head>',f'<style>{css}</style></head>')
    (case/'poster.html').write_text(html)
    render_html_to_png(html,case/'poster.png',scale=2)
    # Actual PNG downsample in Chromium: checks the image, not a responsive DOM relayout.
    thumb=f'<html><body style="margin:0"><img style="width:390px;height:auto;display:block" src="{uri(case/"poster.png","image/png")}"></body></html>'
    render_html_to_png(thumb,case/'mobile-390.png',width=390,height=488,scale=1)


def redraw_saved(directory, name='final'):
    directory=Path(directory).resolve(); case=directory/name
    brief=PosterBrief.model_validate_json((directory/'brief.json').read_text())
    spec=PosterDesignSpec.model_validate_json((case/'spec.json').read_text())
    direction=json.loads((case/'direction.json').read_text())
    background=render_html_to_png((case/'background.html').read_text(),case/'background.png',scale=1)
    draw(directory,case,brief,spec,background,(case/'direction.css').read_text(),
         direction['copy_ink'],direction.get('emphasis_word'))
    return case/'poster.png'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument("--replay-final",action="store_true")
    args=parser.parse_args();print(redraw_saved(args.directory) if args.replay_final else prepare(args.directory))

if __name__=='__main__':main()
