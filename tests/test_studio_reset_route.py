# -*- coding: utf-8 -*-
"""`POST /studio/reset` — the "open it as if it's the first time" action.

Clears the brand's generated creative (archived, not deleted) and leaves the analysis alone: the
strategy she just re-ran must survive, only the superseded poster/reel go. Hermetic — the handler
is driven directly, no port is bound."""
from __future__ import annotations

import io
import json
from pathlib import Path

from dashboard.server import _Handler


def _post(path: str, body: dict, out_dir: str):
    h = _Handler.__new__(_Handler)
    h.out_dir = out_dir
    raw = json.dumps(body).encode("utf-8")
    h.rfile = io.BytesIO(raw)
    h.headers = {"Content-Length": str(len(raw))}
    h.path = path
    sent: dict = {}
    h.send_response = lambda code: sent.update(code=code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    h._send = lambda code, payload, ctype: sent.update(code=code, payload=payload)
    h.do_POST()
    return sent


def _seed(tmp_path: Path) -> None:
    (tmp_path / "posters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reels").mkdir(parents=True, exist_ok=True)
    (tmp_path / "posters" / "brand_poster.png").write_bytes(b"p")
    (tmp_path / "reels" / "brand_reel.mp4").write_bytes(b"r")
    (tmp_path / "brand_result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "brand_profile.json").write_text("{}", encoding="utf-8")


def test_reset_clears_creative_and_keeps_the_analysis(tmp_path: Path):
    _seed(tmp_path)
    out = _post("/studio/reset", {"slug": "brand"}, str(tmp_path))
    assert out["code"] == 200
    assert json.loads(out["payload"])["archived"] >= 2
    assert not (tmp_path / "posters" / "brand_poster.png").exists()
    assert not (tmp_path / "reels" / "brand_reel.mp4").exists()
    # the strategy she just re-ran is NOT touched
    assert (tmp_path / "brand_result.json").exists()
    assert (tmp_path / "brand_profile.json").exists()


def test_reset_rejects_a_bad_slug(tmp_path: Path):
    _seed(tmp_path)
    assert _post("/studio/reset", {"slug": "../../etc"}, str(tmp_path))["code"] == 400
    assert (tmp_path / "posters" / "brand_poster.png").exists()   # nothing moved


def test_reset_on_a_clean_brand_reports_zero(tmp_path: Path):
    out = _post("/studio/reset", {"slug": "brand"}, str(tmp_path))
    assert out["code"] == 200 and json.loads(out["payload"])["archived"] == 0
