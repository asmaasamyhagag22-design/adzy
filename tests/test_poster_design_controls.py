"""Behavior of compositor controls: script/casing, literal text and usable contrast."""
import pytest
from poster.schemas import PosterBrief, PosterDesignSpec
from poster.template import render_poster_html
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize('label', ['نزِّل Firefox', 'Explore Acme', 'نزّل A&B <now>', 'نزّل MyBrand 2.0'])
def test_cta_preserves_literal_authored_text_case_and_bidi(label):
    html=render_poster_html(PosterBrief(business_name='Acme',headline='الخصوصية مُدمجة',cta_text=label),
        spec=PosterDesignSpec(cta_style='block',show=['headline','cta']))
    with sync_playwright() as p:
        b=p.chromium.launch();page=b.new_page();page.route('**/*',lambda r:r.abort())
        page.set_content(html)
        e=page.locator('.cta-text')
        assert e.inner_text()==label
        assert e.evaluate('(e)=>getComputedStyle(e).textTransform')=='none'
        assert e.get_attribute('dir')=='auto'
        assert page.locator('.cta-text bdi').first.get_attribute('dir')=='ltr'
        assert page.locator('.cta-text now').count()==0
        b.close()


@pytest.mark.parametrize('bg,expected', [('#ff7139','rgb(11, 15, 22)'),('#20123a','rgb(255, 255, 255)'),('#f8f6f4','rgb(11, 15, 22)')])
def test_button_selects_the_higher_contrast_ink(bg,expected):
    html=render_poster_html(PosterBrief(business_name='Acme',headline='Ad',cta_text='Try Acme',primary_color=bg,palette_hex=[bg]),
        spec=PosterDesignSpec(cta_style='block'))
    with sync_playwright() as p:
        b=p.chromium.launch();page=b.new_page();page.route('**/*',lambda r:r.abort());page.set_content(html)
        assert page.locator('.cta-text').evaluate('(e)=>getComputedStyle(e).color')==expected
        b.close()


def test_case_typography_controls_reach_actual_compositor():
    spec=PosterDesignSpec(headline_font_size=108,support_font_size=46,cta_font_size=44,show=['headline','sub','cta'])
    html=render_poster_html(PosterBrief(business_name='Acme',headline='خصوصية',subheadline='معظم المتعقبات',cta_text='نزّل Acme'),spec=spec)
    with sync_playwright() as p:
        b=p.chromium.launch();page=b.new_page();page.route('**/*',lambda r:r.abort());page.set_content(html)
        for selector,expected in [('.headline','108px'),('.sub','46px'),('.cta-text','44px')]:
            assert page.locator(selector).evaluate('(e)=>getComputedStyle(e).fontSize')==expected
        b.close()
