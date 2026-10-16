"""Opt-in real Langfuse SDK observations; no LangChain auto-capture callbacks."""
from contextlib import contextmanager
import hashlib
import os
import re


def mask(data, **kwargs):
    # Allow only protocol tokens and digests, never arbitrary callback/customer strings.
    if isinstance(data, dict):
        return {k: mask(v) for k, v in data.items() if k in {'status','workflow_ref','cost_status','error'}}
    if isinstance(data, str) and (data in {'OK','ERROR','UNKNOWN','PAUSED','BLOCKED','REJECTED','NEEDS_REVIEW','READY_FOR_CREATIVE'} or re.fullmatch('[a-f0-9]{32,64}',data)):
        return data
    return '[REDACTED]'


def create_client(**configuration):
    from langfuse import Langfuse
    from langfuse.span_filter import is_langfuse_span
    from opentelemetry.sdk.trace import TracerProvider
    # Private provider + SDK-only filter prevents unrelated auto-instrumentation export.
    return Langfuse(tracer_provider=TracerProvider(), mask=mask,
                    should_export_span=is_langfuse_span, **configuration)


class Telemetry:
    def __init__(self, client=None):
        self.client = client
        self.trace_id = None
        self._frames = []
        self.events = []  # local protocol outcome evidence, never raw inputs

    @classmethod
    def from_env(cls):
        if os.getenv('ADZY_LANGFUSE_ENABLED') != '1':
            return cls()
        try:
            if not all(os.getenv(k) for k in ('LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL')):
                raise ValueError('Explicit Langfuse configuration required')
            return cls(create_client())
        except Exception:
            return cls()  # observability cannot decide business readiness

    def correlate(self, workflow_id):
        self.trace_id = hashlib.sha256(('adzy.review.v1:' + workflow_id).encode()).hexdigest()[:32]

    def outcome(self, status):
        if self._frames and status in {'OK','ERROR','PAUSED','BLOCKED','REJECTED','NEEDS_REVIEW','READY_FOR_CREATIVE'}:
            self._frames[-1]['status'] = status

    def error(self, ignored):
        self.outcome('ERROR')
        self.events.append({'status':'ERROR'})

    @contextmanager
    def observation(self, name, kind='span'):
        self.events.append({'name':name, 'status':'START'})
        cm = obs = None
        if self.client:
            try:
                cm = self.client.start_as_current_observation(name=name, as_type=kind,
                    trace_context={'trace_id':self.trace_id} if self.trace_id else None,
                    metadata={'workflow_ref':self.trace_id, 'cost_status':'UNKNOWN'})
                obs = cm.__enter__()
            except Exception:
                cm = None
        # Catch before the SDK context manager can automatically record exception messages/PII.
        failure = None
        frame = {'status':'OK'}
        self._frames.append(frame)
        try:
            yield
        except BaseException as exc:
            failure = exc
        self._frames.pop()
        status = ('PAUSED' if type(failure).__name__ == 'GraphInterrupt' else 'ERROR') if failure else frame['status']
        self.events.append({'name':name, 'status':status})
        if cm:
            try:
                obs.update(output={'status':status}, level='ERROR' if failure else 'DEFAULT')
                cm.__exit__(None, None, None)
            except Exception:
                pass
        if failure:
            raise failure

    def flush(self):
        if self.client:
            try: self.client.flush()
            except Exception: pass
