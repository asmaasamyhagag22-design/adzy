# -*- coding: utf-8 -*-
"""Hermetic regression coverage for Phase G's Gallery/Storage reconciliation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HOSTED = _ROOT / "scratchpad" / "hosted"
sys.path.insert(0, str(_HOSTED))

import cutover_checks                                                        # noqa: E402
import phase_g_production_verify as phase_g                                  # noqa: E402


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _folder(name):
    return {"name": name, "id": None, "metadata": None}


def _object(name, size):
    return {"name": name, "id": "object-%s" % size, "metadata": {"size": size}}


class _StorageClient:
    def __init__(self, tree, *, public=False, list_status=200):
        self.tree = tree
        self.public = public
        self.list_status = list_status

    def get(self, _url, **_kwargs):
        return _Response(200, {"public": self.public})

    def post(self, _url, **kwargs):
        request = kwargs["json"]
        if self.list_status != 200:
            return _Response(self.list_status, {"message": "listing failed"})
        prefix, offset = request["prefix"], request["offset"]
        return _Response(200, self.tree.get(prefix, [])[offset:offset + 100])


def _report(client, expected):
    return phase_g.gallery_bucket_report(
        client, base="https://project.supabase.co", bucket="baseera-gallery",
        headers={}, expected_keys=expected)


def test_g5_inventory_is_recursive_excludes_folder_rows_and_reports_extras():
    client = _StorageClient({
        "": [_folder("gallery"), _folder("preexisting")],
        "gallery": [_folder("aa"), _folder("bb")],
        "gallery/aa": [_object("one.png", 11)],
        "gallery/bb": [_object("two.png", 22)],
        "preexisting": [_object("old.png", 33)],
    })

    report = _report(client, ["gallery/aa/one.png", "gallery/bb/two.png"])

    assert report["confirmed"] is True
    assert report["recursive_object_count"] == 3
    assert report["expected_count"] == 2
    assert report["missing_count"] == 0
    assert report["extra_count"] == 1
    assert report["extra"] == ["preexisting/old.png"]
    assert report["digest"] == cutover_checks.manifest_digest([
        {"name": "gallery/aa/one.png", "metadata": {"size": 11}},
        {"name": "gallery/bb/two.png", "metadata": {"size": 22}},
        {"name": "preexisting/old.png", "metadata": {"size": 33}},
    ])[0]


def test_g5_listing_error_fails_closed():
    client = _StorageClient({}, list_status=401)

    with pytest.raises(cutover_checks.StorageListingFailed, match="HTTP 401"):
        _report(client, ["gallery/aa/one.png"])


def test_g5_missing_expected_object_is_failed():
    client = _StorageClient({
        "": [_folder("gallery")],
        "gallery": [_folder("aa")],
        "gallery/aa": [_object("one.png", 11)],
    })

    report = _report(client, ["gallery/aa/one.png", "gallery/bb/missing.png"])

    assert report["confirmed"] is False
    assert report["missing_count"] == 1
    assert report["missing"] == ["gallery/bb/missing.png"]


def test_g5_private_bucket_with_no_expected_gallery_cannot_false_green():
    report = _report(_StorageClient({"": []}, public=False), [])

    assert report["public"] is False
    assert report["recursive_object_count"] == 0
    assert report["confirmed"] is False


def test_g5_public_bucket_fails_even_when_every_expected_object_exists():
    client = _StorageClient({"": [_object("one.png", 1)]}, public=True)

    report = _report(client, ["one.png"])

    assert report["missing_count"] == 0
    assert report["confirmed"] is False
