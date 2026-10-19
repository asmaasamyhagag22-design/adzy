"""Render the public-source Arabic design proposal locally with the existing poster compositor.
This preview neither records approval nor creates a canonical accepted Asset.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path


def render(directory):
    from poster.schemas import PosterBrief,PosterDesignSpec
    from poster.template import render_poster_html
    from poster.render_playwright import render_html_to_png
    root=Path(__file__).resolve().parents[1];assets=root/'docs/demo/firefox'
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((assets/'assets.json').read_text())
    for name,source in manifest.items():
        if hashlib.sha256((assets/name).read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('Frozen public artwork changed')
    def uri(name):return 'data:image/svg+xml;base64,'+base64.b64encode((assets/name).read_bytes()).decode()
    background=f'''<html><body style="margin:0;width:1080px;height:1350px;background:#fff4df;overflow:hidden">
    <div style="position:absolute;width:760px;height:760px;left:160px;top:65px;border-radius:50%;background:#ffb280"></div>
    <img src="{uri('artwork.svg')}" style="position:absolute;left:170px;top:95px;width:740px;height:660px;object-fit:contain">
    </body></html>'''
    bg=render_html_to_png(background,directory/'source-background.png',scale=1)
    brief=PosterBrief(business_name='Firefox',headline='الخصوصية مُدمجة',
        subheadline='فنحن نحظر مُعظم المتعقّبات تلقائيًا.',
        cta_text='نزِّل Firefox',cta_url='https://www.firefox.com/ar/',
        palette_hex=['#20123a','#ff7139','#fff4df'],primary_color='#20123a',
        logo_url=uri('logo.svg'),logo_chip='light')
    spec=PosterDesignSpec(layout='bottom_band',logo_corner='top_right',headline_treatment='stacked_hero',
        accent_word='none',cta_style='block',text_align='right',scrim_strength=0,
        show=['logo','headline','sub','cta'],text_box=[.075,.57,.85],logo_xy=[.69,.045],
        negative_space_zone='bottom',accent_hex='#20123a',marketing_archetype='typographic_anchor')
    html=render_poster_html(brief,str(bg),spec=spec,copy_ink='#20123a')
    # Font bytes travel with the HTML; no Google Fonts or account is needed for this proposal.
    import re
    html=re.sub(r'<link[^>]+(?:fonts.googleapis|fonts.gstatic)[^>]*>','',html)
    font_path=Path('/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf')
    if not font_path.exists(): raise RuntimeError('Install fonts-noto-core for the reproducible Arabic preview')
    if font_path.exists():
        font=base64.b64encode(font_path.read_bytes()).decode()
        html=html.replace('</head>',f'<style>@font-face{{font-family:PortfolioArabic;src:url(data:font/ttf;base64,{font})}} .headline,.lw,.sub,.cta{{font-family:PortfolioArabic,sans-serif!important}} .sub{{font-size:30px!important}}</style></head>')
    (directory/'poster-proposal.html').write_text(html)
    path=render_html_to_png(html,directory/'poster-proposal.png',scale=1)
    report={'kind':'AI_DESIGN_REVIEW_PROPOSAL','canonical_accepted':False,'human_approved':False,
        'model_calls':0,'renderer':'poster.template → poster.render_playwright','sources':manifest,
        'brief':brief.model_dump(exclude={'logo_url'}),'spec':spec.model_dump(),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'limitations':['Local design proposal, not a live model output or canonical final download.','Pixel review by AI is separate from authored-copy assessment.']}
    (directory/'preview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',default='.adzy-portfolio/preview')
    print(render(parser.parse_args().directory))

if __name__=='__main__':main()
