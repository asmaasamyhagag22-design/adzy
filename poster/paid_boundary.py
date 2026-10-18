"""Dispatch guard active only inside the reliable canonical HTTP poster operation.
BaseException is intentional: legacy broad Exception fallbacks must not resend ambiguity.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class StopPaidOperation(BaseException):
    def __init__(self, unknown=False, code="POSTER_STAGE_ADMISSION_REFUSED", diagnostic=None):
        self.unknown=unknown
        self.code="POSTER_PROVIDER_OUTCOME_UNKNOWN" if unknown else code
        self.diagnostic=diagnostic


@dataclass
class Scope:
    operations: object
    run_id: str
    call_id: str | None = None
    response_received: bool = False
    policy: object = None

_scope=ContextVar('poster_paid_operation',default=None)


@contextmanager
def paid_scope(operations,run_id):
    token=_scope.set(Scope(operations,run_id))
    try: yield
    finally: _scope.reset(token)


def require_paid_scope(run_id=None):
    scope = _scope.get()
    if scope is None or (run_id is not None and scope.run_id != run_id):
        raise StopPaidOperation(code='SEMANTIC_SHADOW_REQUIRES_PROTECTED_OPERATION')
    return scope


def retain_failure(exc, stage, *, received=None):
    """Attach only safe data; keep the original exception and conservative accounting."""
    from poster.provider_diagnostics import failure_diagnostic
    scope = _scope.get()
    diagnostic = getattr(exc, '_adzy_diagnostic', None) or failure_diagnostic(
        exc, stage=stage, run_id=scope.run_id if scope else None,
        call_id=scope.call_id if scope else None,
        received=(scope.response_received if scope else False) if received is None else received)
    exc._adzy_diagnostic = diagnostic
    if scope and scope.call_id:
        try:
            scope.operations.record_call_diagnostic(scope.call_id, diagnostic)
        except Exception:
            # Never let a diagnostic-storage failure invite a caller retry.
            raise StopPaidOperation(unknown=True, diagnostic=diagnostic) from None
    return diagnostic


def paid_call(method, _stage="", **kwargs):
    scope=_scope.get()
    if scope is None: return method(**kwargs)
    scope.policy = scope.operations.dispatch_policy(scope.run_id)
    scope.call_id = None
    scope.response_received = False
    from google.genai import types
    method_name=method.__name__
    stage=method_name+("/"+_stage if _stage else "")
    # Each SDK invocation may dispatch ONCE. Application-level retries are separately admitted.
    config=kwargs.get('config')
    prior = config.get('http_options') if isinstance(config, dict) else getattr(config, 'http_options', None)
    timeout = prior.get('timeout') if isinstance(prior, dict) else getattr(prior, 'timeout', None)
    options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1), timeout=timeout)
    if isinstance(config,dict): config={**config,'http_options':options}
    elif config is not None: config=config.model_copy(update={'http_options':options})
    else: raise StopPaidOperation()  # unsupported/unbounded call configuration
    kwargs['config']=config
    if scope.policy:
        from poster.dispatch_policy import bounded_call
        try: kwargs = bounded_call(scope.policy, method, stage, kwargs)
        except Exception: raise StopPaidOperation(code='POSTER_DISPATCH_POLICY_REFUSED') from None
        config = kwargs['config']
    if method_name=='generate_content':
        # Disable automatic tool calls, which can send additional unobserved model requests.
        if isinstance(config,dict): config['automatic_function_calling']={'disable':True}
        else: config.automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    try: ident=scope.operations.start_call(scope.run_id,stage,str(kwargs.get('model') or ''))
    except Exception as exc:
        detail=getattr(exc,"detail",None)
        code=detail.get("code","POSTER_STAGE_ADMISSION_REFUSED") if isinstance(detail,dict) else "POSTER_STAGE_ADMISSION_REFUSED"
        raise StopPaidOperation(code=code) from None
    scope.call_id = ident
    try:
        response=method(**kwargs)
    except BaseException as exc:
        diagnostic = retain_failure(exc, stage)
        code=diagnostic['http_status']
        # Explicit Google error rejections; timeouts/5xx/cancellation are ambiguous.
        rejected=code in (400,401,403,404,429)
        ref=diagnostic['request_reference_id']
        try: scope.operations.finish_call(ident,'rejected' if rejected else 'unknown',reference=ref)
        except Exception: raise StopPaidOperation(unknown=True) from None
        if rejected and scope.policy: raise StopPaidOperation(code="POSTER_PROVIDER_REJECTED", diagnostic=diagnostic) from None
        if rejected: raise
        raise StopPaidOperation(unknown=True, diagnostic=diagnostic) from None
    try: scope.operations.finish_call(ident,'received',response=response)
    except Exception: raise StopPaidOperation(unknown=True) from None
    scope.response_received = True
    return response


def external_research_allowed():
    # Canonical reviewed evidence is already available; search-provider billing is unsupported here.
    return _scope.get() is None


def validation_render_options():
    scope = _scope.get()
    return {'upscale':False} if scope and scope.operations.dispatch_policy(scope.run_id) else {}


def checkpoint_candidate(path, **snapshot):
    """Persist each rendered candidate before the next provider admission can stop execution."""
    scope=_scope.get()
    if scope is not None:
        try: scope.operations.record_candidate(scope.run_id,path,**snapshot)
        except Exception:
            raise StopPaidOperation(code='POSTER_CANDIDATE_STORAGE_FAILED') from None
