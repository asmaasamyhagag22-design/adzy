"""Actor-bound adapter over ADZY's canonical PostgreSQL application services."""
import hashlib
import json
from pydantic import TypeAdapter
from poster.strategy_contract import CreativeContractCompiler
from strategy.positioning.contracts import ResearchQuestion, EvidenceRecord, Claim, to_dict
from strategy.positioning.research import evaluate_research_sufficiency


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class ReviewBoundaryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class CampaignReviewServices:
    def __init__(self, persistence, *, actor_id: str, domain_key: str, subject_id: str):
        self.persistence, self.actor_id = persistence, actor_id
        self.domain_key, self.subject_id = domain_key, subject_id

    def context(self):
        c = self.persistence.canonical_strategy_context(
            domain_key=self.domain_key, user_id=self.actor_id, run_id=self.subject_id)
        if not c or c['brand_id'] != c['run_brand_id'] or c['run_kind'] != 'strategy' or c['run_status'] != 'succeeded':
            raise ReviewBoundaryError('NOT_VISIBLE')
        return c

    def subject(self):
        c = self.context()
        canonical = c['output'].get('canonical_strategy', {})
        return dict(subject_id=c['strategy_version_id'], subject_hash=digest(c['output']),
                    profile_id=c['profile_snapshot_id'], evidence_hash=digest(canonical.get('evidence', [])),
                    current=c['is_current'], approval=c['approval_state'])

    def readiness(self):
        c = self.context()
        s = c['output'].get('canonical_strategy', {})
        try:
            q = TypeAdapter(ResearchQuestion).validate_python(s['research_question'])
            evidence = TypeAdapter(tuple[EvidenceRecord, ...]).validate_python(s['evidence'])
            claims = TypeAdapter(tuple[Claim, ...]).validate_python(s['claims'])
            assessment = evaluate_research_sufficiency(q, evidence, claims)
            payload = to_dict(assessment)
            actions = sorted({str(r.resolution_action.value) for r in assessment.requirement_results
                              if r.requirement_id in assessment.blocking_requirement_ids})
            return {'ready': assessment.eligible_for_final_selection is True,
                    'reasons': list(assessment.blocking_requirement_ids), 'required_actions': actions,
                    'policy': payload['policy_id'] + '@' + payload['policy_version']}
        except (KeyError, ValueError, TypeError):
            return {'ready': False, 'reasons': ['RESEARCH_UNVERIFIED'], 'required_actions': ['REPAIR_RESEARCH']}

    def requirements(self):
        c = self.context()
        # Reuse persisted verifier and canonical SWOT approval; do not invent eligibility rules.
        brand = self.persistence.visible_brand(domain_key=self.domain_key, user_id=self.actor_id)
        eligible = c['output'].get('canonical_strategy', {}).get('verification', {}).get('eligible_for_approval') is True
        return {'eligible': eligible, 'swot_approved': bool(brand and brand.get('swot_approved')),
                'required_actions': [] if eligible else ['REPAIR_STRATEGY']}

    def validate(self, state):
        s = self.subject()
        if not s['current'] or any(s[k] != state[k] for k in ('subject_id','subject_hash','profile_id','evidence_hash')):
            raise ReviewBoundaryError('STALE_SUBJECT')
        return s

    def decide(self, state, decision):
        s = self.validate(state)
        if s['approval'] and s['approval'] != decision:
            raise ReviewBoundaryError('DECISION_CHANGED')
        if decision == 'approved':
            reqs = self.requirements()
            if not self.readiness()['ready'] or not reqs['eligible'] or not reqs['swot_approved']:
                raise ReviewBoundaryError('PREREQUISITES_CHANGED')
        # Canonical service itself checks currentness under Brand row lock before idempotency.
        from api.persistence import StaleStrategyApproval, StrategyNotApprovable, StrategyNotReady
        try:
            return self.persistence.decide_strategy(domain_key=self.domain_key, user_id=self.actor_id,
                run_id=state['subject_id'], decision=decision)
        except StaleStrategyApproval as exc:
            raise ReviewBoundaryError('STALE_SUBJECT') from exc
        except (StrategyNotApprovable, StrategyNotReady) as exc:
            raise ReviewBoundaryError('STRATEGY_INELIGIBLE') from exc

    def compile(self, state):
        self.validate(state)
        c = self.context()  # fresh authoritative approval + currentness before compiler
        contract = CreativeContractCompiler().compile(c)
        return {'contract_id': contract.creative_contract_id,
                'contract_hash': digest(contract.model_dump(mode='json')),
                'creative_contract': contract.model_dump(mode='json')}
