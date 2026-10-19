"""Run the real ADZY shell with disposable fictional application data, loopback only."""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID
from .demo import isolate, init, connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init','serve'])
    parser.add_argument('--directory', default='.review-app-demo')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--capture-sdk', action='store_true', help='In-memory SDK exporter; save redacted synthetic spans on shutdown')
    parser.add_argument('--synthetic-posters', choices=['accepted','draft','rejected'], help='Explicit fake render/QA responses, offline demo only')
    args = parser.parse_args()
    isolate()
    directory = Path(args.directory).resolve()
    if args.action == 'init':
        init(directory)
        m = json.loads((directory/'demo.json').read_text())
        from db.models import User
        from api.user_auth import hash_password
        service,_ = connect(m)
        with service._uow_factory() as uow:
            user = uow.session.get(User,UUID(m['actor']))
            user.hashed_password = hash_password('SyntheticDemo42')
            m['email'] = user.email
            uow.commit()
        (directory/'demo.json').write_text(json.dumps(m,indent=2))
        print(json.dumps({'synthetic':True,'email':m['email'],'password':'SyntheticDemo42'}))
        return
    m = json.loads((directory/'demo.json').read_text())
    from db.test_database_safety import require_disposable_test_database
    require_disposable_test_database(m['dsn'],environ={'BASEERA_DESTRUCTIVE_TEST_DATABASE':'baseera_pytest_foundation'})
    if m.get('synthetic') is not True: raise ValueError('Only disposable synthetic demo supported')
    os.environ.update(PERSISTENCE_BACKEND='postgres',BASEERA_DATABASE_URL=m['dsn'],
        BASEERA_WEBAPP_DIR=str(directory/'application'),ADZY_REVIEW_STATE_DIR=str(directory/'review'))
    if args.synthetic_posters:
        os.environ.update(BASEERA_ENABLE_PAID_IMAGES='1', BASEERA_NO_SPEND='1', ADZY_POSTER_RESERVATION_USD='0.6',
            ADZY_POSTER_BUDGET_USD='10', ADZY_POSTER_CALL_ESTIMATES_JSON='{}')
        from .synthetic_poster import install
        install(args.synthetic_posters)
    from api.main import app
    exporter = telemetry = None
    if args.capture_sdk:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from .telemetry import create_client, Telemetry
        from api.routes.review import get_review_telemetry
        exporter = InMemorySpanExporter()
        telemetry = Telemetry(create_client(public_key='pk-synthetic-local-app',secret_key='sk-synthetic',
            base_url='http://127.0.0.1:9',span_exporter=exporter))
        def save_capture():
            telemetry.flush()
            (directory/'sdk-capture.json').write_text(json.dumps([
                {'name':s.name,'trace_id':format(s.context.trace_id,'032x'),
                 'attributes':dict(s.attributes),'duration_ns':s.end_time-s.start_time}
                for s in exporter.get_finished_spans()],indent=2))
        class CaptureTelemetry(Telemetry):
            def flush(self):
                super().flush()
                save_capture()
        app.dependency_overrides[get_review_telemetry] = lambda:CaptureTelemetry(telemetry.client)
        from contextlib import asynccontextmanager
        original_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def capture_lifespan(application):
            try:
                async with original_lifespan(application):
                    yield
            finally:
                save_capture()
                telemetry.client.shutdown()
        app.router.lifespan_context = capture_lifespan
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port)


if __name__ == '__main__': main()
