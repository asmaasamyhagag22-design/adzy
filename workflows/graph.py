from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from .contracts import ReviewState, Decision
from .tools import inspection_tools


def build_graph(services, saver, telemetry, after_approval=None):
    tools = inspection_tools(services, telemetry)
    def call(name):
        r = tools[name].invoke({}, config={'callbacks': []})
        if r['status'] != 'OK':
            from .services import ReviewBoundaryError
            raise ReviewBoundaryError(r['error'])
        return r['data']

    def assess(s):
        subject = call('get_review_subject')
        readiness = call('inspect_research_readiness')
        reqs = call('inspect_creative_contract_requirements')
        refs = {k: subject[k] for k in ('subject_id','subject_hash','profile_id','evidence_hash')}
        if not subject['current']:
            return {**refs, 'status':'BLOCKED', 'reasons':['STALE_SUBJECT']}
        if not readiness['ready'] or not reqs['eligible'] or not reqs['swot_approved']:
            return {**refs, 'status':'BLOCKED', 'reasons':readiness['reasons'] +
                    ([] if reqs['eligible'] else ['STRATEGY_INELIGIBLE']) +
                    ([] if reqs['swot_approved'] else ['SWOT_NOT_APPROVED']),
                    'required_actions':readiness['required_actions'] + reqs['required_actions'] +
                    ([] if reqs['swot_approved'] else ['REVIEW_SWOT_IN_APPLICATION'])}
        status = ('REJECTED' if subject['approval']=='rejected' else
                  'READY_FOR_CREATIVE' if subject['approval']=='approved' else 'NEEDS_REVIEW')
        return {**refs, 'status':status, 'required_actions':
                ['REVIEW_STRATEGY'] if status=='NEEDS_REVIEW' else
                ['GENERATE_NEW_STRATEGY'] if status=='REJECTED' else []}

    def _review(s):
        answer = interrupt({'subject_id':s['subject_id'], 'subject_hash':s['subject_hash'],
                            'status':'NEEDS_REVIEW'}, response_schema=Decision)
        d = Decision.model_validate(answer)
        if d.subject_id != s['subject_id'] or d.subject_hash != s['subject_hash']:
            from .services import ReviewBoundaryError
            raise ReviewBoundaryError('REVIEW_SUBJECT_MISMATCH')
        return {'decision':d.model_dump()}

    def review(s):
        with telemetry.observation('human_review'):
            return _review(s)

    def apply(s):
        services.decide(s, s['decision']['decision'])
        if after_approval: after_approval()  # fault injection seam, never an alternate writer
        return {'status':'REJECTED' if s['decision']['decision']=='rejected' else 'READY_FOR_CREATIVE',
                'required_actions':['GENERATE_NEW_STRATEGY'] if s['decision']['decision']=='rejected' else []}

    def compile_contract(s):
        # Research and eligibility can change independently of the workflow cursor.
        ready = call('inspect_research_readiness')
        reqs = call('inspect_creative_contract_requirements')
        if not ready['ready'] or not reqs['eligible'] or not reqs['swot_approved']:
            return {'status':'BLOCKED', 'reasons':['PREREQUISITES_CHANGED']}
        return {**services.compile(s), 'status':'READY_FOR_CREATIVE'}

    def safe(name, fn):
        def run(s):
            with telemetry.observation(name):
                try:
                    result = fn(s)
                    telemetry.outcome(result.get('status','OK'))
                    return result
                except Exception as exc:
                    telemetry.error(type(exc).__name__)
                    return {'status':'BLOCKED', 'reasons':[getattr(exc,'code',getattr(exc,'reason_code','SERVICE_ERROR'))],
                            'required_actions':['INSPECT_AUTHORITATIVE_STATE']}
        return run
    g = StateGraph(ReviewState)
    g.add_node('assess', safe('assess', assess))
    g.add_node('human_review', review)  # GraphInterrupt must propagate; no writes here
    g.add_node('apply_decision', safe('apply_decision', apply))
    g.add_node('compile_contract', safe('compile_contract', compile_contract))
    g.add_edge(START, 'assess')
    g.add_conditional_edges('assess', lambda s:s['status'],
        {'NEEDS_REVIEW':'human_review', 'READY_FOR_CREATIVE':'compile_contract', 'BLOCKED':END,'REJECTED':END})
    g.add_edge('human_review','apply_decision')
    g.add_conditional_edges('apply_decision',lambda s:s['status'],
        {'READY_FOR_CREATIVE':'compile_contract','REJECTED':END,'BLOCKED':END})
    g.add_edge('compile_contract',END)
    return g.compile(checkpointer=saver)
