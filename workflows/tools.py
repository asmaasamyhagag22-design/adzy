"""Typed deterministic read tools; authority is closed over, never a tool argument."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from langchain_core.tools import StructuredTool

class InspectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    view: Literal['current'] = 'current'

class InspectionResult(BaseModel):
    status: Literal['OK', 'ERROR']
    data: dict
    error: str = ''


def inspection_tools(services, telemetry):
    result = {}
    for name, method in [('inspect_research_readiness', services.readiness),
                         ('get_review_subject', services.subject),
                         ('inspect_creative_contract_requirements', services.requirements)]:
        def inspect(view='current', _method=method, _name=name):
            with telemetry.observation(_name, kind='tool'):
                try:
                    return InspectionResult(status='OK', data=_method()).model_dump()
                except Exception as exc:
                    telemetry.error(type(exc).__name__)
                    return InspectionResult(status='ERROR', data={},
                        error=getattr(exc, 'code', 'INSPECTION_FAILED')).model_dump()
        result[name] = StructuredTool.from_function(inspect, name=name,
            description=f'Inspect actor-scoped ADZY {name}; read only.', args_schema=InspectionInput)
    return result
