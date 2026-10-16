# -*- coding: utf-8 -*-
"""Hermetic regressions for Gate-1 closure Storage stage 2."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEY = "sb_secret_hermetic_storage_key_123456789"


@pytest.fixture(scope="module")
def closure():
    path = ROOT / "scratchpad" / "hosted" / "gate1_closure.py"
    spec = importlib.util.spec_from_file_location("gate1_closure_storage_tests", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, status_code, payload, content=b""):
        self.status_code = status_code
        self.payload = payload
        self.content = content

    def json(self):
        return self.payload


def folder(name):
    return {"name": name, "id": None, "metadata": None}


def obj(name, size):
    return {"name": name, "id": "id-%s" % name, "metadata": {"size": size}}


class StorageClient:
    def __init__(self, tree, *, metadata=None, metadata_status=200):
        self.tree = tree
        self.metadata = metadata if metadata is not None else {
            "id": "baseera-gallery", "name": "baseera-gallery", "public": False}
        self.metadata_status = metadata_status
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return Response(self.metadata_status, self.metadata)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        request = kwargs["json"]
        page = self.tree.get(request["prefix"], [])
        offset = request["offset"]
        return Response(200, page[offset:offset + 100])


def expected_rows(objects):
    return [{"storage_key": item["name"], "checksum": "unused", "byte_size":
             item["metadata"]["size"]} for item in objects]


def test_opaque_secret_uses_apikey_only_and_legacy_jwt_retains_bearer(closure):
    assert closure._storage_api_headers(SECRET_KEY) == {"apikey": SECRET_KEY}
    legacy = "eyJheader123.payload123.signature123"
    assert closure._storage_api_headers(legacy) == {
        "apikey": legacy, "Authorization": "Bearer %s" % legacy}
    with pytest.raises(closure.ClosureFailed, match="not a supported Storage API credential"):
        closure._storage_api_headers("opaque-but-not-a-supabase-api-key")


def test_metadata_uses_exact_files_storage_get_contract_and_parses_private(closure, capsys):
    client = StorageClient({})
    headers = closure._storage_api_headers(SECRET_KEY)

    metadata = closure._retrieve_bucket_metadata(
        client, base="https://project.supabase.co/", bucket="baseera-gallery",
        headers=headers, known_secrets=(SECRET_KEY,))

    assert metadata["public"] is False
    assert len(client.get_calls) == 1
    url, kwargs = client.get_calls[0]
    assert url == "https://project.supabase.co/storage/v1/bucket/baseera-gallery"
    assert kwargs["headers"] == {"apikey": SECRET_KEY}
    assert SECRET_KEY not in url
    output = capsys.readouterr().out
    assert "STORAGE_METADATA_METHOD=GET" in output
    assert "STORAGE_METADATA_PATH=/storage/v1/bucket/baseera-gallery" in output
    assert SECRET_KEY not in output


def test_safe_400_is_redacted_and_cannot_produce_metadata(closure, capsys):
    client = StorageClient({}, metadata_status=400, metadata={
        "error": "Unauthorized", "message": "Invalid key %s" % SECRET_KEY})

    with pytest.raises(closure.ClosureFailed, match="HTTP 400"):
        closure._retrieve_bucket_metadata(
            client, base="https://project.supabase.co", bucket="baseera-gallery",
            headers=closure._storage_api_headers(SECRET_KEY),
            known_secrets=(SECRET_KEY,))

    output = capsys.readouterr().out
    assert "STORAGE_METADATA_STATUS=400" in output
    assert "STORAGE_METADATA_ERROR_CODE=Unauthorized" in output
    assert "STORAGE_METADATA_ERROR_MESSAGE=Invalid key <supabase-secret-redacted>" in output
    assert SECRET_KEY not in output


def test_inventory_retains_recursive_pagination_folder_filter_and_digest(closure):
    nested = [obj("file-%03d.bin" % index, index + 1) for index in range(101)]
    client = StorageClient({"": [folder("gallery")], "gallery": nested})
    canonical = [{"name": "gallery/%s" % item["name"],
                  "metadata": item["metadata"]} for item in nested]

    report = closure._storage_inventory(
        client, base="https://project.supabase.co", bucket="baseera-gallery",
        headers=closure._storage_api_headers(SECRET_KEY),
        expected_rows=expected_rows(canonical), known_secrets=(SECRET_KEY,))

    expected_digest = hashlib.sha256("\n".join(
        "%s|%s" % (item["name"], item["metadata"]["size"])
        for item in canonical).encode("utf-8")).hexdigest()
    assert report["object_count"] == 101
    assert report["digest"] == expected_digest
    assert report["missing"] == []
    assert report["extra"] == []
    gallery_offsets = [call[1]["json"]["offset"] for call in client.post_calls
                       if call[1]["json"]["prefix"] == "gallery"]
    assert gallery_offsets == [0, 100]


def test_public_bucket_and_private_bucket_without_expected_objects_cannot_pass(closure):
    public = StorageClient({"": [obj("one.bin", 1)]}, metadata={
        "id": "baseera-gallery", "public": True})
    with pytest.raises(closure.ClosureFailed, match="not proven private"):
        closure._storage_inventory(
            public, base="https://project.supabase.co", bucket="baseera-gallery",
            headers={}, expected_rows=[{"storage_key": "one.bin"}])

    empty = StorageClient({"": []})
    with pytest.raises(closure.ClosureFailed, match="no migrated Gallery blob keys"):
        closure._storage_inventory(
            empty, base="https://project.supabase.co", bucket="baseera-gallery",
            headers={}, expected_rows=[])


def test_missing_expected_object_fails_and_successful_retrieval_can_pass(closure):
    client = StorageClient({"": [obj("present.bin", 1)]})
    with pytest.raises(closure.ClosureFailed, match="1 expected managed"):
        closure._storage_inventory(
            client, base="https://project.supabase.co", bucket="baseera-gallery",
            headers={}, expected_rows=[{"storage_key": "missing.bin"}])

    successful = StorageClient({"": [obj("present.bin", 1)]})
    inventory = closure._storage_inventory(
        successful, base="https://project.supabase.co", bucket="baseera-gallery",
        headers={}, expected_rows=[{"storage_key": "present.bin"}])
    report = {
        "bucket_public": False, "missing_count": len(inventory["missing"]),
        "recursive_object_count": inventory["object_count"],
        "exported_object_count": inventory["object_count"],
        "manifest_digest": inventory["digest"],
        "exported_manifest_digest": inventory["digest"],
    }
    assert closure._storage_backup_pass(report) is True
