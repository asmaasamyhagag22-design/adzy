"""Bounded operational diagnostics. Never serialize exceptions or provider bodies."""
import re
import os


def _safe_path(value):
    """Keep public request/local-schema identifiers, never arbitrary provider strings."""
    if not isinstance(value,str): return None
    public=set(('GenerateContentRequest generationConfig generation_config responseSchema response_schema '
        'thinkingConfig thinking_config thinkingLevel thinking_level maxOutputTokens max_output_tokens '
        'contents role parts systemInstruction properties items required propertyOrdering property_ordering '
        'additionalProperties additional_properties minItems maxItems min_items max_items minimum maximum '
        'minLength maxLength min_length max_length pattern type enum anyOf nullable temperature topP topK '
        'execution_status propositions coverage proposition_id proposition_ids span field start end text '
        'copy_hash copy_sources source source_ref source_quote source_text '
        'subject asserted_fact qualifiers quantity location conditions timeframe comparison negation kind '
        'explanation judgments verdict citations evidence_id quote same_entity conditions_preserved '
        'negation_preserved strength_preserved response_mime_type responseMimeType').split())
    tokens=re.findall(r'[A-Za-z_][A-Za-z_0-9]*|\d+',value[:512])
    if not tokens or tokens[0] not in public: return None
    return '.'.join(t if t in public else '[index]' if t.isdecimal() else '[redacted-field]' for t in tokens[:24])


def _constraint(value):
    if not isinstance(value,str): return None
    text=value[:8192].lower()
    patterns=(
        (r'too many states|schema.{0,60}(?:too complex|too complicated|complexity)|complexity.{0,30}schema',
         'Response schema exceeds provider complexity/state limit.'),
        (r'property.?ordering.{0,100}(?:not.{0,20}propert|unknown|missing)',
         'propertyOrdering contains a key absent from properties.'),
        (r'property.?ordering.{0,100}(?:unique|duplicate)',
         'propertyOrdering keys must be unique.'),
        (r'not supported|unsupported', 'Parameter or constraint is unsupported for this request/model.'),
        (r'unknown (?:name|field)|unrecognized field', 'Unknown request/schema field.'),
        (r'(?:required field|missing required|must be specified)', 'A required request/schema field is missing.'),
    )
    for pattern,summary in patterns:
        if re.search(pattern,text): return summary
    bound=re.search(r'(?:must be|must contain|must have)\s+(at most|at least|less than|greater than|no more than)\s+(\d{1,6})\b',text)
    if bound and int(bound[2])<=65536:
        return f'Constraint: {bound[1]} {int(bound[2])}.'
    expected=re.search(r'(?:expected|must be)\s+(?:an?\s+)?(array|object|string|integer|boolean|number)\b',text)
    if expected: return 'Expected value type: '+expected[1]+'.'
    return None


def _actionable(exc):
    # SDK APIError.details is the decoded original error envelope, not details[] itself.
    # Inspect it here in memory; neither envelope nor free-form descriptions leave this function.
    envelope=exc.details if isinstance(exc.details,dict) else {}
    error=envelope.get('error',envelope)
    error=error if isinstance(error,dict) else {}
    message=exc.message if isinstance(exc.message,str) else ''
    paths=[];explanations=[];violations=[];infos=[]
    roots=r'(?:generationConfig|generation_config|responseSchema|response_schema|thinkingConfig|thinking_config|thinking_level|thinkingLevel|contents|propertyOrdering|property_ordering)'
    for match in re.finditer(roots+r'(?:\.[A-Za-z_][A-Za-z_0-9]*|\[[\w\x27\x22]+\])*',message[:8192]):
        path=_safe_path(match[0])
        if path and path not in paths: paths.append(path)
    explanation=_constraint(message)
    if explanation: explanations.append(explanation)
    details=error.get('details',[])
    for detail in (details[:16] if isinstance(details,list) else []):
        if not isinstance(detail,dict): continue
        if detail.get('@type')=='type.googleapis.com/google.rpc.BadRequest':
            fields=detail.get('fieldViolations',detail.get('field_violations',[]))
            for violation in (fields[:12] if isinstance(fields,list) else []):
                if not isinstance(violation,dict): continue
                path=_safe_path(violation.get('field'))
                explanation=_constraint(violation.get('description'))
                if path or explanation:
                    violations.append({'field':path,'constraint':explanation,
                        'safe_detail_missing':not bool(explanation)})
                    if path and path not in paths: paths.append(path)
                    if explanation and explanation not in explanations: explanations.append(explanation)
        elif detail.get('@type')=='type.googleapis.com/google.rpc.ErrorInfo':
            reasons={'SCHEMA_TOO_COMPLEX','RESPONSE_SCHEMA_TOO_COMPLEX','INVALID_ARGUMENT','INVALID_REQUEST',
                'UNSUPPORTED_PARAMETER','INVALID_GENERATION_CONFIG','SERVICE_DISABLED','API_KEY_INVALID',
                'ACCESS_TOKEN_SCOPE_INSUFFICIENT','MODEL_NOT_FOUND','REQUEST_TOO_LARGE','QUOTA_EXCEEDED',
                'RATE_LIMIT_EXCEEDED','FIELD_VIOLATION'}
            reason=detail.get('reason');domain=detail.get('domain')
            infos.append({'reason':reason if isinstance(reason,str) and reason in reasons else None,
                'domain':domain if domain in ('googleapis.com','aiplatform.googleapis.com','generativelanguage.googleapis.com') else None})
    return {'parameter_paths':paths[:16],'constraint_explanations':explanations[:12],
        'bad_request_field_violations':violations[:12],'error_info':infos[:8],
        'safe_detail_available':bool(paths or explanations or any(i['reason'] for i in infos)),
        'safe_detail_status':'RECOGNIZED_SAFE_DETAIL' if paths or explanations or any(i['reason'] for i in infos)
                             else 'NO_RECOGNIZED_SAFE_PARAMETER_OR_CONSTRAINT_DETAIL'}


def failure_diagnostic(exc, *, stage, run_id=None, call_id=None, received=False):
    from google.genai.errors import APIError
    import httpx
    from pydantic import ValidationError

    api = isinstance(exc, APIError)
    code = exc.code if api and isinstance(exc.code, int) else None
    canonical = exc.status if api else None
    statuses = {'INVALID_ARGUMENT', 'UNAUTHENTICATED', 'PERMISSION_DENIED', 'NOT_FOUND',
                'RESOURCE_EXHAUSTED', 'INTERNAL', 'UNAVAILABLE', 'DEADLINE_EXCEEDED',
                'FAILED_PRECONDITION', 'CANCELLED', 'UNKNOWN', 'OUT_OF_RANGE', 'ABORTED',
                'UNIMPLEMENTED', 'DATA_LOSS', 'ALREADY_EXISTS'}
    if not isinstance(canonical, str) or canonical not in statuses:
        canonical = None
    if received:
        kind = 'INVALID_STRUCTURED_OUTPUT'
    elif api:
        kind = 'PROVIDER_API_REJECTION' if code and 400 <= code < 500 else 'PROVIDER_API_UNKNOWN_OUTCOME'
    elif isinstance(exc, httpx.TimeoutException):
        kind = 'TRANSPORT_TIMEOUT_UNKNOWN_OUTCOME'
    elif isinstance(exc, httpx.TransportError):
        kind = 'TRANSPORT_UNKNOWN_OUTCOME'
    elif isinstance(exc, (ValueError, TypeError, ValidationError)):
        kind = 'LOCAL_CONFIGURATION_OR_SCHEMA_FAILURE'
    else:
        kind = 'LOCAL_OR_TRANSPORT_FAILURE_UNKNOWN_OUTCOME'

    # A provider message can echo credentials, prompts or evidence in arbitrary prose.
    # Retain only recognized reason categories and known public parameter names.
    message = exc.message if api and isinstance(exc.message, str) else ''
    folded = message[:8192].lower()
    reason = next((safe for match, safe in (
        ('permission', 'Provider reports insufficient permission'),
        ('quota', 'Provider reports quota exhaustion'),
        ('rate limit', 'Provider reports a rate limit'),
        ('not supported', 'Provider reports an unsupported setting'),
        ('unsupported', 'Provider reports an unsupported setting'),
        ('invalid', 'Provider reports an invalid argument'),
        ('unavailable', 'Provider reports service unavailable'),
    ) if match in folded), 'Provider message withheld (unrecognized free-form text)' if api else kind)
    parameters = [p for p in ('thinking_level', 'thinkingLevel', 'maxOutputTokens',
                             'responseSchema', 'temperature', 'topP', 'topK') if p in message[:8192]]
    if parameters:
        reason += '; parameter(s): ' + ', '.join(parameters)
    actionable=_actionable(exc) if api else {}
    if actionable.get('constraint_explanations'):
        reason=' '.join(actionable['constraint_explanations'])[:768]
        if parameters: reason+='; parameter(s): '+', '.join(parameters)
    headers = getattr(getattr(exc, 'response', None), 'headers', {}) or {}
    ref = headers.get('x-request-id') or headers.get('x-goog-request-id') if api else None
    # Only opaque IDs, never arbitrary header text or request/authorization headers.
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ref):
        ref = None
    if ref and any(len(value) >= 8 and value in ref for key, value in os.environ.items()
                   if any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY','PROJECT','CREDENTIAL'))):
        ref = None
    return {'version': 'adzy.provider-failure@2', 'failure_stage': stage[:120],
            'exception_class': type(exc).__name__[:80], 'classification': kind,
            'http_status': code, 'provider_canonical_status': canonical,
            'safe_message': reason, 'message_policy': 'bounded_public_paths_and_constraint_summaries',
            'request_reference_id': ref, 'run_id': run_id, 'call_id': call_id,
            'successful_response_received': received, **actionable}
