"""Reusable synchronous service. Actor identity must come from the caller's authentication.
SQLite is a local workflow store, never the application's approval database.
"""
from contextlib import contextmanager, closing
import sqlite3
from pathlib import Path
from uuid import uuid4
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .contracts import VERSION, Decision
from .graph import build_graph
from .services import CampaignReviewServices, ReviewBoundaryError
from .telemetry import Telemetry


def setup(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory/'bindings.sqlite')) as c, c:
        c.execute('CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, actor TEXT NOT NULL, domain TEXT NOT NULL, subject TEXT NOT NULL, version TEXT NOT NULL)')
    with SqliteSaver.from_conn_string(str(directory/'checkpoints.sqlite')) as saver:
        saver.setup()


class ReviewWorkflow:
    def __init__(self, directory, persistence, *, actor_id, telemetry=None, after_approval=None):
        self.directory, self.persistence, self.actor_id = Path(directory), persistence, str(actor_id)
        self.telemetry = telemetry or Telemetry.from_env()
        self.after_approval = after_approval

    @contextmanager
    def _execution(self, workflow_id, create=None, reuse=False):
        # A dedicated local gate keeps the lock across the durable binding commit and
        # first checkpoint. SQLite connections/transactions close on every exit.
        with closing(sqlite3.connect(self.directory/'execution.sqlite', timeout=30)) as gate, gate:
            gate.execute('BEGIN IMMEDIATE')
            with self._bound_execution(workflow_id,create,reuse) as execution:
                yield execution

    @contextmanager
    def _bound_execution(self, workflow_id, create=None, reuse=False):
        # Separate registry transaction serializes local entry points across processes.
        # Checkpoints use another file; no approval truth is stored here.
        with closing(sqlite3.connect(self.directory/'bindings.sqlite', timeout=30)) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if create and reuse:
                existing = c.execute('SELECT id FROM workflows WHERE actor=? AND domain=? AND subject=? AND version=? ORDER BY rowid LIMIT 1',
                                     (self.actor_id,*create,VERSION)).fetchone()
                if existing:
                    workflow_id, create = existing[0], None
            if create:
                c.execute('INSERT INTO workflows VALUES (?,?,?,?,?)',
                    (workflow_id,self.actor_id,*create,VERSION))
            row = c.execute('SELECT actor,domain,subject,version FROM workflows WHERE id=?',(workflow_id,)).fetchone()
            if row is None or row[0] != self.actor_id:
                raise ReviewBoundaryError('NOT_VISIBLE')
            if row[3] != VERSION: raise ReviewBoundaryError('INCOMPATIBLE_VERSION')
            services = CampaignReviewServices(self.persistence, actor_id=self.actor_id, domain_key=row[1], subject_id=row[2])
            services.context()  # authorization on start/status/resume, independent of thread ID
            if create:
                c.commit()
                c.execute('BEGIN IMMEDIATE')

            self.telemetry.correlate(workflow_id)
            with SqliteSaver.from_conn_string(str(self.directory/'checkpoints.sqlite')) as saver:
                graph = build_graph(services,saver,self.telemetry,self.after_approval)
                config = {'configurable':{'thread_id':workflow_id},'callbacks':[], 'metadata':{}}
                config['metadata']['new_binding'] = bool(create)
                # Explicit context disables LangSmith even if inherited env enables it.
                from langsmith import tracing_context
                with tracing_context(enabled=False):
                    yield graph,config,services
            c.commit()

    def start(self, *, domain_key, subject_id, reuse=False):
        ident = str(uuid4())
        with self._execution(ident, (domain_key,subject_id), reuse=reuse) as (graph,config,services):
            if reuse and not config['metadata']['new_binding']:
                existing = self._checked_snapshot(graph,config,services)
                # Explicit recovery of a terminal compiler block uses the same exact
                # subject and persisted approval. It never replays apply_decision.
                swot_recovery = (not existing.get('decision')
                    and existing.get('reasons') == ['SWOT_NOT_APPROVED']
                    and services.requirements()['swot_approved'])
                compiler_recovery = (existing.get('reasons') == ['PROOF_MESSAGE_MISMATCH']
                    and existing.get('decision', {}).get('decision') == 'approved')
                if (existing.get('status') != 'BLOCKED' or existing.get('pending_nodes')
                        or not (swot_recovery or compiler_recovery)):
                    return existing
                subject = services.validate(existing)
                if compiler_recovery:
                    d = existing['decision']
                    if (subject['approval'] != 'approved'
                            or d['subject_id'] != existing['subject_id']
                            or d['subject_hash'] != existing['subject_hash']):
                        raise ReviewBoundaryError('DECISION_CHANGED')
                ident = existing['workflow_id']
            with self.telemetry.observation('review_segment'):
                graph.invoke({'version':VERSION,'workflow_id':ident,'status':'NEEDS_REVIEW',
                              'reasons':[],'required_actions':[]},config,durability='sync')
            result = self._snapshot(graph,config)
        self.telemetry.flush()
        return result

    @staticmethod
    def _snapshot(graph,config):
        snapshot = graph.get_state(config)
        return {**snapshot.values, 'pending_nodes':list(snapshot.next)}

    def _checked_snapshot(self, graph, config, services):
        result = self._snapshot(graph,config)
        if not result.get('version'):
            return {'workflow_id': config['configurable']['thread_id'], 'version': VERSION,
                    'status':'FAILED', 'reasons':['START_FAILED'],
                    'required_actions':['INSPECT_AUTHORITATIVE_STATE'], 'pending_nodes':[]}
        if result['version'] != VERSION: raise ReviewBoundaryError('INCOMPATIBLE_VERSION')
        if not result.get('subject_id'):
            return {**result, 'status':'FAILED', 'reasons':['START_FAILED'],
                    'required_actions':['INSPECT_AUTHORITATIVE_STATE']}
        try:
            services.validate(result)
            if result.get('status') == 'READY_FOR_CREATIVE':
                if not services.readiness()['ready'] or not services.requirements()['swot_approved']:
                    raise ReviewBoundaryError('PREREQUISITES_CHANGED')
                result.update(services.compile(result))
        except Exception as exc:
            return {**result,'status':'BLOCKED','reasons':[getattr(exc,'code','PREREQUISITES_CHANGED')],
                    'contract_id':'','contract_hash':'','creative_contract':None}
        return result

    def status(self, workflow_id):
        with self._execution(workflow_id) as (graph,config,services):
            return self._checked_snapshot(graph,config,services)

    def resume(self, workflow_id, decision):
        d = Decision.model_validate(decision)
        with self._execution(workflow_id) as (graph,config,services):
            state = self._snapshot(graph,config)
            if not state.get('subject_id'): raise ReviewBoundaryError('START_FAILED')
            if state.get('version') != VERSION: raise ReviewBoundaryError('INCOMPATIBLE_VERSION')
            if d.subject_id != state['subject_id'] or d.subject_hash != state['subject_hash']:
                raise ReviewBoundaryError('REVIEW_SUBJECT_MISMATCH')
            try: subject = services.validate(state)
            except ReviewBoundaryError as exc:
                return {**state,'status':'BLOCKED','reasons':[exc.code], 'contract_id':'','contract_hash':''}
            if state.get('decision') and state['decision'] != d.model_dump():
                raise ReviewBoundaryError('DECISION_CHANGED')
            with self.telemetry.observation('review_segment'):
                if not state['pending_nodes']:
                    if not state.get('decision') and subject['approval'] and subject['approval'] != d.decision:
                        raise ReviewBoundaryError('DECISION_CHANGED')
                    # Repeated resume is read-only, but readiness is never returned from stale cache.
                    if state['status']=='READY_FOR_CREATIVE':
                        try:
                            if not services.readiness()['ready'] or not services.requirements()['swot_approved']:
                                raise ReviewBoundaryError('PREREQUISITES_CHANGED')
                            state.update(services.compile(state))
                        except Exception: state.update(status='BLOCKED',reasons=['PREREQUISITES_CHANGED'],contract_id='',contract_hash='')
                    result = state
                else:
                    graph.invoke(Command(resume=d.model_dump()) if 'human_review' in state['pending_nodes'] else None,
                                 config,durability='sync')
                    result = self._snapshot(graph,config)
        self.telemetry.flush()
        return result
