"""P-68 (CJK content-density), P-69 (checkout line-items are not offerings), P-65 (robots retry)."""
from __future__ import annotations

import urllib.error

from scraper.extractors.content_quality import _is_meaningful
from scraper.extractors.next_data import _looks_like_menu_item


# ---- P-68: space-less scripts (D-8) ---------------------------------------
def test_chinese_paragraph_is_meaningful_despite_no_spaces():
    zh = "我们是一家位于上海的精品咖啡店每天新鲜烘焙精选咖啡豆欢迎光临品尝我们的手冲咖啡和特色甜点现在就在线订购享受优质服务"
    assert _is_meaningful(zh) is True


def test_thai_paragraph_is_meaningful():
    th = "เราเป็นร้านกาแฟพิเศษในกรุงเทพเราคั่วเมล็ดกาแฟคุณภาพสูงทุกวันเชิญลิ้มลองกาแฟดริปและของหวานพิเศษสั่งซื้อออนไลน์ได้แล้ววันนี้"
    assert _is_meaningful(th) is True


def test_english_nav_list_still_rejected():
    assert _is_meaningful("Home Menu About Contact Blog") is False


# ---- P-69: checkout line-items are not offerings --------------------------
def test_shipping_line_item_is_not_an_offering():
    assert _looks_like_menu_item({"name": "Express Shipping", "price": 50}) is False
    assert _looks_like_menu_item({"title": "VAT", "price": 14}) is False
    assert _looks_like_menu_item({"name": "شحن سريع", "price": 30}) is False
    assert _looks_like_menu_item({"name": "رسوم توصيل", "price": 25}) is False


def test_real_menu_item_still_detected():
    assert _looks_like_menu_item({"name": "Grilled Kofta Plate", "price": 180}) is True
    assert _looks_like_menu_item({"title": "Cappuccino", "total_price": 45}) is True


def test_category_without_price_still_rejected():
    assert _looks_like_menu_item({"name": "Hot Drinks"}) is False


# ---- P-65: robots fetch retries transient failures ------------------------
def test_robots_retries_transient_then_succeeds(monkeypatch):
    from scraper import robots as R
    calls = {"n": 0}

    class _Resp:
        def read(self): return b"User-agent: *\nDisallow: /admin\n"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _flaky_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("temporary blip")  # transient
        return _Resp()

    monkeypatch.setattr(R.urllib.request, "urlopen", _flaky_urlopen)
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)
    body = R._fetch_robots_body("https://example.com/robots.txt", timeout=1.0)
    assert "Disallow: /admin" in body
    assert calls["n"] == 2  # retried once


def test_robots_404_is_not_retried(monkeypatch):
    from scraper import robots as R
    calls = {"n": 0}

    def _404(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(R.urllib.request, "urlopen", _404)
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)
    try:
        R._fetch_robots_body("https://example.com/robots.txt", timeout=1.0)
        assert False, "should raise"
    except urllib.error.HTTPError:
        pass
    assert calls["n"] == 1  # definitive 4xx -> no retry
