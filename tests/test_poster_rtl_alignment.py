"""Measure actual compositor boxes; direction must not invert physical right alignment."""
import pytest
from poster.schemas import PosterBrief,PosterDesignSpec
from poster.template import render_poster_html


@pytest.mark.parametrize('headline,align',[('الخصوصية مُدمجة','right'),('Privacy by default','right'),('Privacy by default','left')])
def test_freeform_copy_keeps_requested_physical_edge(headline,align):
    from playwright.sync_api import sync_playwright
    brief=PosterBrief(business_name='Public Demo',headline=headline,cta_text='Learn more')
    spec=PosterDesignSpec(text_box=[.075,.57,.85],text_align=align,show=['headline','cta'])
    html=render_poster_html(brief,spec=spec)
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        page=browser.new_page(viewport={'width':1080,'height':1350})
        page.route('https://**/*',lambda route:route.abort())
        page.set_content(html)
        assert page.locator('html').get_attribute('lang') == ('ar' if headline.startswith('ال') else 'und')
        positions=page.evaluate('''() => {
            const box=document.querySelector('.lower'), h=document.querySelector('.headline,.hero-headline,.lockup-headline'), c=document.querySelector('.cta');
            const b=box.getBoundingClientRect(),hr=h.getBoundingClientRect(),cr=c.getBoundingClientRect(),s=getComputedStyle(box);
            return {left:b.left+parseFloat(s.paddingLeft),right:b.right-parseFloat(s.paddingRight),
                headline:{left:hr.left,right:hr.right},cta:{left:cr.left,right:cr.right,bottom:cr.bottom}};
        }''')
        browser.close()
    for part in ('headline','cta'):
        assert positions[part][align]==pytest.approx(positions[align],abs=1)
    assert positions['cta']['bottom']<=1350
