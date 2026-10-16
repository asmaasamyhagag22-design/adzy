from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys

from strategy.positioning.verifier import PositioningDecisionVerifier

from test_gate2b_positioning_contracts import _verifier_fixture


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "frontend" / "assets" / "strategy-state.js"
CSS = ROOT / "frontend" / "assets" / "adzy.css"


def _state_eval(expression: str):
    javascript = (
        "const state=require(process.argv[1]);"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run(
        ["node", "-e", javascript, str(STATE)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_empty_proof_points_never_duplicate_the_primary_message():
    result = _state_eval(
        "state.proofLine({primary_message:'Say this',reason_to_believe:'Say this',proof_points:[]})"
    )
    assert result == {
        "text": "No separate consumer-facing proof approved yet.",
        "approved": False,
    }


def test_evidence_roles_do_not_flatten_a_limitation_into_positive_proof():
    result = _state_eval("""state.groupEvidenceRoles([
      {claim_id:'complaint',statement:'One customer complaint',scope:{role:'CUSTOMER_RELEVANCE'},
       evidence_strength:'WEAK',limitations:['one observation, not prevalence'],supporting_evidence_ids:['voc']},
      {claim_id:'metric',statement:'CTA count is above peers',scope:{role:'COMPETITIVE_DIFFERENTIATION'},
       evidence_strength:'STRONG',supporting_evidence_ids:['peer']}
    ],[
      {evidence_id:'voc',source_class:'CUSTOMER_VOICE',limitations:['do not generalize']},
      {evidence_id:'peer',source_class:'COMPETITOR_ANALYSIS'}
    ]).map(g=>({key:g.key,consumerFacing:g.items.map(i=>i.role.consumerFacing)}))""")
    assert result == [
        {"key": "differentiation", "consumerFacing": [False]},
        {"key": "limitation", "consumerFacing": [False]},
    ]


def test_frontend_customer_voice_projection_does_not_fabricate_missing_voc():
    assert _state_eval("state.findCustomerVoice({claims:[],evidence:[]})") is None
    assert _state_eval("state.findCustomerVoice({claims:[{supporting_evidence_ids:['brand']}],evidence:[{evidence_id:'brand',source_class:'BRAND_OWNED',excerpt:'Buy now'}]})") is None
    found = _state_eval("state.findCustomerVoice({claims:[{claim_id:'c1',supporting_evidence_ids:['voc']}],evidence:[{evidence_id:'voc',source_class:'CUSTOMER_VOICE',availability:'AVAILABLE',excerpt:'Real quote'}]})")
    assert found["evidence"]["excerpt"] == "Real quote"


def test_duplicate_canonical_positioning_alternatives_are_detected_and_blocked():
    evidence, claims, question, decision = _verifier_fixture()
    first = decision.options[0]
    duplicate = replace(
        first,
        option_id="duplicate",
        trade_offs=("Different numbering or trade-off text must not disguise duplication.",),
        why_it_may_be_rejected="A differently worded rejection note.",
    )
    duplicate_decision = replace(decision, options=(first, duplicate))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=duplicate_decision,
    )
    assert result.status.value == "NEEDS_REPAIR"
    assert result.eligible_for_approval is False
    assert "POSITIONING_ALTERNATIVES_NOT_DISTINCT" in {
        defect["code"] for defect in result.defects
    }


@lru_cache(maxsize=1)
def _mobile_metrics() -> dict:
    script = r'''
import json, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

css = Path(sys.argv[1]).read_text(encoding="utf-8")
css = re.sub(r"@import url\([^;]+;", "", css, count=1)
html = """
<body class="adzy-ready"><div class="adzy-app">
  <header class="adzy-topbar"><div class="adzy-logo-slot"><button class="adzy-logo-fallback">adzy</button></div>
    <nav class="adzy-nav"><button>Discover</button><button>Think</button><button aria-current="page">Strategy</button><button>Work</button></nav>
    <div class="adzy-top-actions"><select class="adzy-brand-select"><option>Buffalo Burger</option></select></div></header>
  <main class="adzy-main"><section class="adzy-stage adzy-strategy-stage is-active"><div class="adzy-container">
    <div class="adzy-strategy-question"><h1 class="adzy-page-title">So...<br>what should Buffalo Burger<br>stand for?</h1><p class="adzy-research-question">A real question that must wrap without clipping.</p></div>
    <div class="adzy-directions-head"><h2 class="adzy-section-title">Directions</h2><div class="adzy-lens"><button>Direction</button><button>Proof</button><button>Watch-outs</button></div></div>
    <div class="adzy-directions has-selection"><article class="adzy-direction is-selected"><h3>Convenient mobile app with deals and personalization</h3><p class="adzy-direction-target"><span>For</span>customers seeking loyalty rewards and a deliberately long qualification</p></article></div>
    <div id="meaningful-end" style="height:24px">End of meaningful content</div>
  </div></section></main>
</div></body>"""
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.set_content(html)
    page.add_style_tag(content=css)
    page.locator("#meaningful-end").scroll_into_view_if_needed()
    metrics = page.evaluate("""() => {
      const nav=document.querySelector('.adzy-nav').getBoundingClientRect();
      const end=document.querySelector('#meaningful-end').getBoundingClientRect();
      const app=getComputedStyle(document.querySelector('.adzy-app'));
      const select=document.querySelector('.adzy-brand-select');
      return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,
        reserve:parseFloat(app.paddingBottom),navHeight:nav.height,
        overlap:Math.max(0,Math.min(nav.bottom,end.bottom)-Math.max(nav.top,end.top)),
        selectorText:select.options[select.selectedIndex].textContent,
        selectorWidth:select.getBoundingClientRect().width,
        offenders:Array.from(document.querySelectorAll('*')).map(el=>{const r=el.getBoundingClientRect();return {tag:el.tagName,cls:el.className,right:r.right,left:r.left,width:r.width}}).filter(r=>r.right>innerWidth+.5||r.left<-.5).slice(0,12)};
    }""")
    browser.close()
print(json.dumps(metrics))
'''
    python = Path(sys.executable)
    playwright_temp = ROOT / "artifacts" / ".playwright-p1-temp"
    playwright_temp.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TEMP"] = environment["TMP"] = str(playwright_temp)
    result = subprocess.run(
        [str(python), "-c", script, str(CSS)],
        check=True, capture_output=True, text=True, timeout=45, env=environment,
    )
    return json.loads(result.stdout)


def test_mobile_strategy_has_no_horizontal_overflow_at_390px():
    metrics = _mobile_metrics()
    assert metrics["viewport"] == 390
    assert metrics["scrollWidth"] <= metrics["viewport"], metrics["offenders"]
    assert metrics["selectorText"] == "Buffalo Burger"
    assert metrics["selectorWidth"] >= 160


def test_mobile_bottom_nav_reserves_space_and_does_not_cover_content():
    metrics = _mobile_metrics()
    assert metrics["reserve"] >= metrics["navHeight"] + 32
    assert metrics["overlap"] == 0
    assert "env(safe-area-inset-bottom)" in CSS.read_text(encoding="utf-8")


def test_approval_success_state_requires_the_exact_approved_version():
    result = _state_eval("({pending:state.isActuallyApproved({strategy_version_id:'run-1',strategy_approval_state:'pending'}),approvedWithoutVersion:state.isActuallyApproved({strategy_approval_state:'approved'}),approved:state.isActuallyApproved({strategy_version_id:'run-1',strategy_approval_state:'approved'})})")
    assert result == {"pending": False, "approvedWithoutVersion": False, "approved": True}
