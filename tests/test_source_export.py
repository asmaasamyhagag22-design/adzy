"""Behavioral packaging checks; preserve private/local files while exporting only intended inputs."""
import importlib.util
from pathlib import Path
import sys
import json

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from prepare_app_publication import prepare, release_test_paths, source_candidate


def test_export_omits_historical_logs_manifests_and_unselected_tests(tmp_path):
    target=tmp_path/'public'
    local=ROOT/'tests/test_voiceover.py'
    before=local.read_bytes() if local.exists() else None
    prepare(target)
    manifest=json.loads((target/'PUBLICATION_MANIFEST.json').read_text())
    names={row['path'] for row in manifest['files']}
    assert 'poster/copy_editor.py' in names and 'tests/test_copy_editor.py' in names
    assert 'tests/copy_editor_review_fixture.py' in names
    assert 'tools/verify_copy_editor_browser.py' in names
    assert len([n for n in names if n.startswith('poster/fonts/')])==4
    assert not any(n.startswith('docs/validation/copy-editor/') for n in names)
    assert not any(n.endswith(('.log','.sqlite','.jsonl')) for n in names)
    assert not any(n.startswith('docs/architecture/') and n.endswith(('.json','.txt')) for n in names)
    assert 'tests/test_voiceover.py' not in names
    assert not manifest['secret_scan']['findings']
    assert not manifest['public_document_links']['broken']
    # The single old audit PNG and frozen source context are required regression fixtures.
    assert 'docs/upgrade_audit/evidence/claim_assessment/rendered_escape.png' in names
    assert 'docs/experiments/poster_compiler_source_support_20261018/context.json' in names
    if before is not None:
        assert local.read_bytes()==before  # export never removes or edits local sources


def test_release_helper_closure_keeps_imported_fixtures():
    names={p.name for p in release_test_paths(ROOT)}
    assert {'test_copy_editor.py','test_review_workflow.py','poster_boundary_support.py',
            'test_poster_oneshot_best_attempt.py','copy_editor_review_fixture.py'}<=names
    assert len(names)<80


def test_nested_runtime_and_symlink_sources_are_not_candidates(tmp_path):
    for rel in ('api/.cache/leak.py','api/outputs/customer.py','api/node_modules/nested.py'):
        path=tmp_path/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('private')
        assert not source_candidate(path,tmp_path)
        assert path.read_text()=='private'
    nested=tmp_path/'api/source-bundle';nested.mkdir();(nested/'PUBLICATION_MANIFEST.json').write_text('{}')
    nested_source=nested/'api.py';nested_source.write_text('private')
    assert not source_candidate(nested_source,tmp_path)
    outside=tmp_path/'secret.py';outside.write_text('private')
    link=tmp_path/'api/linked.py';link.symlink_to(outside)
    assert not source_candidate(link,tmp_path)
