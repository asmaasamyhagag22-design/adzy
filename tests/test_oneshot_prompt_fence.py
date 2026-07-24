# -*- coding: utf-8 -*-
"""1.3 (owner report 2026-07-19, "في شغل في البرومبت"): the poster painted CONTRACT wording into
the artwork because the image prompt embedded the VERBATIM_RENDER_CONTRACT paragraph + quoted
field labels. Pins: the image prompt carries a clean BEGIN TEXT/END TEXT fence holding ONLY the
copy strings; the contract survives ONLY in the OCR read-back path; the compliance line (CS-02
residual) now reaches the oneshot engine as an unquoted directive."""
from __future__ import annotations

import re

from poster.contracts import VERBATIM_RENDER_CONTRACT, compliance_for
from poster.oneshot import build_oneshot_prompt

_COPY = {"headline": "تعلمي البرمجة في 6 شهور", "subheadline": "معتمد من الوزارة",
         "cta": "سجلي دلوقتي"}


def _p(**kw):
    return build_oneshot_prompt(_COPY, brand_name="NTI", rtl=True, **kw)


def test_no_contract_prose_in_image_prompt():
    p = _p()
    for leak in ("VERBATIM TEXT", "FROZEN strings", "character-for-character",
                 "render & read", "fidelity failure"):
        assert leak not in p, leak
    assert VERBATIM_RENDER_CONTRACT not in p


def test_fence_holds_only_the_copy_strings():
    p = _p()
    assert "BEGIN TEXT" in p and "END TEXT" in p
    fence = p.split("BEGIN TEXT")[1].split("END TEXT")[0]
    lines = [ln for ln in fence.splitlines() if ln.strip()]
    assert lines == [f'"{_COPY["headline"]}"', f'"{_COPY["subheadline"]}"', f'"{_COPY["cta"]}"']
    # the quoted labels that used to get painted are gone from the drawable zone
    assert 'HEADLINE (dominant' not in p and 'CALL-TO-ACTION (on a pill button):' not in p
    # and the final ban references the fence BY NAME, not a vague "above"
    assert "between BEGIN TEXT and END TEXT" in p


def test_roles_survive_outside_the_fence_unquoted():
    p = _p()
    assert "TYPOGRAPHY ROLES" in p and "pill button" in p
    assert p.index("TYPOGRAPHY ROLES") < p.index("BEGIN TEXT")


def test_compliance_line_reaches_oneshot_for_regulated_category():
    line = compliance_for("clinic")
    assert line                                     # clinic IS a regulated category
    p = _p(category="clinic")
    assert line in p
    assert p.index(line) < p.index("BEGIN TEXT")    # directive, not drawable text
    assert compliance_for("") == "" and line not in _p()


def test_contract_still_pins_the_ocr_readback():
    import inspect
    import poster.oneshot as osh
    src = inspect.getsource(osh)
    # the OCR read-back prompt still carries the contract (it is a READER instruction there)
    assert "VERBATIM_RENDER_CONTRACT" in src.split("def build_oneshot_prompt")[0] or \
           "VERBATIM_RENDER_CONTRACT" in src.split("END TEXT")[-1]


def test_partial_copy_fence_shrinks_honestly():
    p = build_oneshot_prompt({"headline": "عنوان بس"})
    fence = p.split("BEGIN TEXT")[1].split("END TEXT")[0]
    assert [ln for ln in fence.splitlines() if ln.strip()] == ['"عنوان بس"']
    assert "subheadline" not in fence.lower()
