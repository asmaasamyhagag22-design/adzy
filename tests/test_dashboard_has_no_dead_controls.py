# -*- coding: utf-8 -*-
"""A control that cannot do its job must not be on the screen.

OWNER RULING 2026-07-25: «لو شايف فيتشر او حاجه ملهاش لازم شيها».

REGION EDITING was the clearest case. "Edit a region" opened a brush, let you paint a mask, let
you type what to change — and then returned your own picture back, because the route it calls
cannot be served:

    api/routes/generate.py: "Region editing needs a paid Imagen edit call
    (poster/imagen_edit_provider.py) and every Imagen model 404s on this project, so the request
    cannot be served."

An earlier fix made it HONEST (`applied: false` plus a note in the user's language) rather than
silently returning the input. Honest, and still a control that costs a user their time to discover
it does nothing. The same reasoning already retired the product reference-photo upload, whose own
label admitted it "does not condition the render yet".

WHAT IS DELIBERATELY KEPT: the `/api/edit/inpaint` ROUTE. Deleting a route breaks any client
already calling it, and its honest `applied:false` answer is the right response to such a call.
What goes is the UI that invites a user into a dead end.

This file is a regression lock, not a description of a fix: it fails if a control that cannot work
comes back onto the page.
"""
from __future__ import annotations

from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
    encoding="utf-8")


# --- region editing is gone from the UI ---------------------------------------------------------

def test_no_region_edit_button():
    assert "toggleMaskMode" not in HTML, (
        "the 'Edit a region' control is back; the route it calls cannot serve an edit")


def test_no_mask_canvas_or_brush():
    for dead in ("gen-mask-canvas", "gen-mask-panel", "mask-brush-size", "mask-edit-prompt",
                 "mask-apply-btn"):
        assert dead not in HTML, f"{dead} is back on the page"


def test_the_frontend_no_longer_calls_the_inpaint_route():
    """Checked against the CODE, not the comments. The note explaining why the control was removed
    names the route it used to call — a record of the decision, not a caller of it, and a test that
    cannot tell those apart forbids writing the explanation down."""
    import re
    code = re.sub(r"<!--.*?-->", " ", HTML, flags=re.S)
    code = re.sub(r"^\s*//[^\n]*$", " ", code, flags=re.M)
    assert "/api/edit/inpaint" not in code


def test_the_route_itself_is_kept_and_still_answers_honestly():
    """A client that already calls it gets `applied: false` and a reason — the correct answer to a
    request we cannot serve. Removing the endpoint would turn that into a 404."""
    src = (Path(__file__).resolve().parents[1] / "api" / "routes" / "generate.py").read_text(
        encoding="utf-8")
    assert "/edit/inpaint" in src
    assert "applied" in src


# --- the reference-photo upload stays retired ----------------------------------------------------

def test_the_dead_reference_photo_upload_stays_gone():
    assert "studio-product-reference" not in HTML, (
        "an input whose own label said it does not affect the render is back")


def test_its_replacement_is_present():
    """It was removed because the product LINK does what it only appeared to do."""
    assert "studio-product-url" in HTML


# --- nothing else on the page promises what it cannot do -----------------------------------------

def test_no_control_advertises_itself_as_unavailable():
    """A button labelled with 'coming soon' / 'not available' is a dead control by its own
    admission. Comments explaining history are fine; visible copy is not."""
    import re
    visible = re.sub(r"<!--.*?-->", " ", HTML, flags=re.S)          # drop HTML comments
    visible = re.sub(r"//[^\n]*", " ", visible)                     # drop JS line comments
    for phrase in ("coming soon", "not implemented", "does nothing"):
        assert phrase not in visible.lower(), phrase


def test_every_onclick_resolves_to_a_defined_function():
    """A button wired to a function that does not exist is the deadest control of all."""
    import re
    handlers = set(re.findall(r'onclick="([a-zA-Z_$][\w$]*)\s*\(', HTML))
    missing = [h for h in sorted(handlers)
               if f"function {h}(" not in HTML and f"{h} = function" not in HTML
               and f"const {h}" not in HTML and f"let {h}" not in HTML]
    assert not missing, f"buttons calling undefined functions: {missing}"
