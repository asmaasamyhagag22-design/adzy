"""Coherent fictional evidence for the assembled ADZY product slice; no customer data."""
def field(value, url='https://studio.example.test/process'):
    return {'value': value, 'source_type': 'extracted', 'confidence': 'high',
            'evidence': [{'page_url': url, 'quote': value, 'extractor': 'synthetic_fixture'}]}


def brand(domain='studio_example_test'):
    return {'domain_key': domain, '_profile': {
        'source_url': 'https://studio.example.test', 'name': field('Studio Fixture'),
        'category': field('Business services'),
        'value_propositions': [field('Guided specialist onboarding'), field('Transparent delivery milestones')],
        'trust_signals': [field('Published guided specialist onboarding process'),
                          field('Published transparent delivery milestones process')],
        'audience_signals': [field('Operations teams managing launch deadlines')],
        'languages': [{'code': 'en', 'proportion': 1}],
        'locations': [{'country': 'Qatar'}],
    }, '_full_result': {
        'generated_at': '2026-10-16T00:00:00Z',
        'marketing_intel': {'pain_points': [field('Specialist onboarding handoffs are difficult to coordinate', 'https://voice.example.test/comment/1')]},
        'competitor_intel': {'Synthetic peer': {'usps': [field('Peer onboarding uses a self-service catalog', 'https://peer.example.test/about')]}}}}

