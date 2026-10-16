(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.BaseeraStrategyState = api;
  root.AdzyStrategyState = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const text = (value) => value === null || value === undefined ? '' : String(value).trim();
  const list = (value) => Array.isArray(value) ? value.filter(Boolean) : [];

  function normalizedMaterial(value) {
    return text(value).toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  }

  function exists(strategy) {
    const value = strategy || {};
    const canonical = !!value.strategy_version_id && value.approval_state === 'approved';
    const readableLegacy = !value.strategy_version_id && !!value.strategic_posture;
    return canonical || readableLegacy;
  }

  function isActuallyApproved(brand) {
    const value = brand || {};
    return text(value.strategy_approval_state).toLowerCase() === 'approved'
      && !!text(value.strategy_version_id);
  }

  function proofLine(contract) {
    const points = list(contract && contract.proof_points)
      .map((proof) => text(proof && (proof.statement || proof.value || proof)))
      .filter(Boolean);
    return {
      text: points.join(' · ') || 'No separate consumer-facing proof approved yet.',
      approved: points.length > 0,
    };
  }

  function optionSignature(option) {
    const value = option || {};
    return [
      value.target,
      value.frame_of_reference,
      value.consumer_value || value.differentiated_value,
      value.differentiated_value,
      value.reason_to_believe,
      value.positioning_statement,
    ].map(normalizedMaterial).join('|');
  }

  function duplicateAlternativeIndexes(options) {
    const firstBySignature = new Map();
    const duplicates = new Map();
    list(options).forEach((option, index) => {
      const signature = optionSignature(option);
      if (!signature.replace(/\|/g, '')) return;
      if (firstBySignature.has(signature)) duplicates.set(index, firstBySignature.get(signature));
      else firstBySignature.set(signature, index);
    });
    return duplicates;
  }

  function findCustomerVoice(candidate) {
    const value = candidate || {};
    const evidenceById = new Map(list(value.evidence).map((row) => [text(row.evidence_id), row]));
    for (const claim of list(value.claims)) {
      for (const evidenceId of list(claim.supporting_evidence_ids)) {
        const evidence = evidenceById.get(text(evidenceId));
        if (evidence && text(evidence.source_class).toUpperCase() === 'CUSTOMER_VOICE'
            && text(evidence.availability || 'AVAILABLE').toUpperCase() === 'AVAILABLE'
            && text(evidence.excerpt || evidence.raw_value)) {
          return { claim, evidence };
        }
      }
    }
    return null;
  }

  function evidenceRole(claim, evidence) {
    const value = claim || {};
    const row = evidence || {};
    const scopeRole = text(value.scope && value.scope.role).toUpperCase();
    const sourceClass = text(row.source_class).toUpperCase();
    const limitationText = [
      ...list(value.limitations), ...list(row.limitations),
    ].map(text).join(' ').toLowerCase();
    const limited = text(value.evidence_strength).toUpperCase() === 'WEAK'
      || list(value.contradicting_evidence_ids).length > 0
      || /(single|one) (customer )?(signal|observation)|not prevalence|do not generalize|non-general/.test(limitationText);
    if (limited) return { key: 'limitation', label: 'Limitation / counter-signal', tone: 'warning', consumerFacing: false };
    if (scopeRole === 'CUSTOMER_RELEVANCE' || scopeRole === 'TARGET') {
      return { key: 'target', label: 'Supports target', tone: 'target', consumerFacing: false };
    }
    if (scopeRole === 'BRAND_CAPABILITY' || scopeRole === 'REASON_TO_BELIEVE') {
      return { key: 'capability', label: 'Supports capability', tone: 'capability', consumerFacing: false };
    }
    if (scopeRole === 'COMPETITIVE_DIFFERENTIATION' || sourceClass === 'COMPETITOR_ANALYSIS') {
      return { key: 'differentiation', label: 'Supports differentiation', tone: 'differentiation', consumerFacing: false };
    }
    return { key: 'internal', label: 'Internal strategic evidence', tone: 'internal', consumerFacing: false };
  }

  function groupEvidenceRoles(claims, evidence) {
    const evidenceById = new Map(list(evidence).map((row) => [text(row.evidence_id), row]));
    const order = ['target', 'capability', 'differentiation', 'limitation', 'internal'];
    const grouped = new Map(order.map((key) => [key, []]));
    list(claims).forEach((claim) => {
      const rows = list(claim.supporting_evidence_ids).map((id) => evidenceById.get(text(id))).filter(Boolean);
      const paired = rows.length ? rows : [null];
      paired.forEach((evidenceRow) => {
        const role = evidenceRole(claim, evidenceRow);
        grouped.get(role.key).push({ claim, evidence: evidenceRow, role });
      });
    });
    return order.map((key) => ({
      key,
      label: (grouped.get(key)[0] && grouped.get(key)[0].role.label) || ({
        target: 'Supports target', capability: 'Supports capability',
        differentiation: 'Supports differentiation', limitation: 'Limitation / counter-signal',
        internal: 'Internal strategic evidence',
      }[key]),
      items: grouped.get(key),
    })).filter((group) => group.items.length);
  }

  return Object.freeze({
    exists,
    isActuallyApproved,
    proofLine,
    optionSignature,
    duplicateAlternativeIndexes,
    findCustomerVoice,
    evidenceRole,
    groupEvidenceRoles,
  });
}));
