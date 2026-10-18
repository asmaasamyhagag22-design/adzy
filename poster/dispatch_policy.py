"""Optional, frozen policy on the existing operation; never a financial authorization."""
import json
from datetime import date, datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Route(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: Literal['vertex', 'developer']
    model: str
    stage: str
    location: str
    project: str
    estimate_usd: str
    max_output_tokens: int = Field(gt=0, le=4096)
    thinking_level: Literal['MINIMAL', 'LOW'] | None = None
    project_schema: bool = False
    max_image_dimension: int = Field(default=1536, gt=0, le=3072)
    max_images: int = Field(default=3, ge=0, le=3)
    max_image_bytes: int = Field(default=4_000_000, gt=0, le=7_000_000)


class DispatchPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    plan_id: str = Field(min_length=16)
    expires_on: str
    max_dispatches: int = Field(gt=0, le=12)
    request_hashes: dict[Literal['ar','en'], str]
    routes: list[Route] = Field(min_length=1)

    @field_validator('expires_on')
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    def route(self, stage, model):
        if datetime.now(timezone.utc).date().isoformat() > self.expires_on:
            raise ValueError('POSTER_POLICY_EXPIRED')
        matches = [r for r in self.routes if r.stage == stage and r.model == model]
        if len(matches) != 1:
            raise ValueError('POSTER_ROUTE_NOT_LISTED')
        return matches[0]


def bounded_call(policy, method, stage, kwargs):
    """Inspect the actual SDK client and bound the outbound request without a token probe."""
    from google.genai import types
    route = policy.route(stage, kwargs.get('model'))
    client = method.__self__._api_client
    provider = 'vertex' if client.vertexai else 'developer'
    if (provider, client.location or '', client.project or '') != (route.provider, route.location, route.project):
        raise ValueError('POSTER_PROVIDER_ROUTE_CHANGED')
    options = client._http_options
    if options.api_version != ('v1beta1' if provider == 'vertex' else 'v1beta'):
        raise ValueError('POSTER_API_VERSION_CHANGED')
    expected_host = ('https://aiplatform.googleapis.com/' if route.location == 'global' else
                     f'https://{route.location}-aiplatform.googleapis.com/') if provider == 'vertex' else 'https://generativelanguage.googleapis.com/'
    if options.base_url != expected_host:
        raise ValueError('POSTER_ENDPOINT_CHANGED')
    if method.__name__ != 'generate_content':
        raise ValueError('POSTER_ROUTE_NOT_LISTED')
    config = kwargs['config']
    config = types.GenerateContentConfig(**config) if isinstance(config, dict) else config.model_copy(deep=True)
    # No paid tools, remote references, caching or hidden candidate multiplication.
    if config.routing_config or config.service_tier or config.tools or config.cached_content or config.response_modalities and 'IMAGE' not in [str(getattr(m,'value',m)) for m in config.response_modalities]:
        raise ValueError('POSTER_UNBOUNDED_CONFIG')
    config.http_options.timeout = min(config.http_options.timeout or 120000, 120000)
    config.candidate_count = 1
    config.max_output_tokens = min(config.max_output_tokens or route.max_output_tokens, route.max_output_tokens)
    image_output = 'IMAGE' in [str(getattr(m,'value',m)) for m in config.response_modalities or []]
    config.thinking_config = (None if image_output else types.ThinkingConfig(thinking_level=route.thinking_level)
                              if route.thinking_level else types.ThinkingConfig(thinking_budget=1024))
    if route.thinking_level:
        config.temperature = config.top_p = config.top_k = None
        config.frequency_penalty = config.presence_penalty = None
    if route.project_schema and isinstance(config.response_schema, type):
        from poster.claim_assessment import provider_schema_projection
        config.response_schema = provider_schema_projection(config.response_schema)
    if image_output and config.max_output_tokens > 1536:
        raise ValueError('POSTER_IMAGE_OUTPUT_LIMIT')
    if config.image_config and (config.image_config.image_size or config.image_config.aspect_ratio not in (None,'1:1','4:5')):
        raise ValueError('POSTER_IMAGE_CONFIG_LIMIT')
    images = []
    def inspect(value):
        if isinstance(value, bytes):
            images.append(value)
            return '<inline image>'
        if isinstance(value, dict):
            if any(value.get(k) for k in ('file_data','file_uri','fileData','fileUri','video_metadata','videoMetadata','executable_code','code_execution_result','function_call','function_response')):
                raise ValueError('POSTER_REMOTE_OR_TOOL_CONTENT')
            return {k:inspect(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)): return [inspect(v) for v in value]
        if hasattr(value,'model_dump'): return inspect(value.model_dump(exclude_none=True))
        if isinstance(value,type) and hasattr(value,'model_json_schema'): return value.model_json_schema()
        return value
    payload = inspect({'contents':kwargs['contents'], 'config':config})
    # UTF-8/schema byte bound, not a claim of measured tokenizer usage. Image inputs bounded separately.
    if len(json.dumps(payload,ensure_ascii=False).encode()) > 24000 or len(images) > route.max_images:
        raise ValueError('POSTER_INPUT_LIMIT')
    from PIL import Image
    from io import BytesIO
    for raw in images:
        if len(raw) > route.max_image_bytes: raise ValueError('POSTER_IMAGE_INPUT_LIMIT')
        with Image.open(BytesIO(raw)) as image:
            if max(image.size) > route.max_image_dimension or image.width*image.height > route.max_image_dimension**2:
                raise ValueError('POSTER_IMAGE_INPUT_LIMIT')
    return {**kwargs, 'config':config}
