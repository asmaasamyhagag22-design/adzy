# -*- coding: utf-8 -*-
"""An empty profile must stop the run — never be dressed up as findings about the brand.

MEASURED (pepsi.com, 2026-07-21). The run finished "successfully" and produced a SWOT whose
weaknesses read:

    "No clear brand tagline established on-site"
    "Thin value proposition — few distinct benefits stated"
    "Narrow offering range surfaced on-site"
    "Shallow site presence — evidence from a single page"

Every one of those is FALSE about Pepsi. The profile behind them had `name=None`, `category=None`,
`description=None` and zero offerings — they are symptoms of OUR extraction returning nothing,
re-labelled as facts about the brand. A client would read them as an audit.

`full_run.py:220` already refuses a 0-page crawl for exactly this reason ("a hollow profile and a
garbage dashboard that LOOKS successful"). The same guard was missing one step later: a crawl can
succeed, or a fallback can half-succeed, and extraction still yield nothing. Hermetic."""
from __future__ import annotations

from competitor.full_run import profile_is_hollow


class _P:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_a_profile_with_no_identity_is_hollow():
    assert profile_is_hollow(_P(name=None, category=None, description=None, offerings=[]))


def test_a_named_brand_is_not_hollow():
    assert not profile_is_hollow(_P(name="Pepsi", category=None, description=None, offerings=[]))


def test_offerings_alone_are_enough():
    assert not profile_is_hollow(_P(name=None, category=None, description=None,
                                    offerings=[{"name": "Pepsi Max"}]))


def test_a_category_alone_is_enough():
    assert not profile_is_hollow(_P(name=None, category="beverage", description=None,
                                    offerings=[]))


def test_evidenced_field_wrappers_are_unwrapped():
    # profiles carry {"value": ...} wrappers on the CLI path and bare values on the API path
    assert not profile_is_hollow(_P(name={"value": "Pepsi"}, category=None,
                                    description=None, offerings=[]))
    assert profile_is_hollow(_P(name={"value": ""}, category={"value": None},
                                description=None, offerings=[]))


def test_none_is_hollow():
    assert profile_is_hollow(None)
