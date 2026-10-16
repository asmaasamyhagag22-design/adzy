"""Isolated targeted validation; never loads private .env or calls external providers."""
import sys
from .demo import isolate

def main():
    isolate()
    import os
    os.environ['BASEERA_DATABASE_URL']=''
    os.environ['BASEERA_TEST_DATABASE_URL']=''
    if os.environ.get('ADZY_WORKFLOW_TEST_DSN'):
        from db.test_database_safety import require_disposable_test_database
        dsn = os.environ['ADZY_WORKFLOW_TEST_DSN']
        require_disposable_test_database(dsn)
        os.environ['BASEERA_TEST_DATABASE_URL'] = dsn
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:] or ['-q','tests/test_review_workflow.py']))

if __name__=='__main__': main()
