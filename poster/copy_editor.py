"""Trusted compositor recipes and exact-copy checks. No provider or browser authority."""
import base64
import hashlib
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from pydantic import BaseModel, ConfigDict, Field
from poster.schemas import PosterBrief, PosterDesignSpec
from poster.claim_assessment import assess_copy, digest
from poster.strategy_contract import PosterStrategyVerifier


class CopyEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    headline: str = Field(min_length=2, max_length=160)
    subheadline: str = Field(default='', max_length=260)
    cta_text: str = Field(min_length=1, max_length=80)


def font_manifest():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).with_name('fonts').glob('*.ttf'))}


def capture_recipe(result):
    """Only a classic text-free background + real structured inputs are editable."""
    from api import gallery_store
    if getattr(result, 'engine', 'classic') != 'classic' or not getattr(result, 'spec', None):
        return None
    try:
        brief = PosterBrief.model_validate(result.brief.model_dump())
        if brief.logo_url and not brief.logo_url.startswith('data:image/'):
            from poster.template import _remote_image_data_uri
            logo = _remote_image_data_uri(brief.logo_url)
            if not logo: return None
            brief = brief.model_copy(update={'logo_url': logo})
        bg = gallery_store.store(Path(result.background_path).read_bytes())
        recipe = {'version': 1, 'fonts':font_manifest(), 'brief': brief.model_dump(mode='json'),
                  'spec': result.spec.model_dump(mode='json'),
                  'background': {'storage_key': bg.storage_key, 'sha256': bg.checksum, 'bytes': bg.byte_size, 'mime': bg.mime},
                  'inputs': getattr(result, 'rendering_inputs', None) or {}}
        return {**recipe, 'hash': digest(recipe)}
    except (ValueError, OSError, AttributeError):
        return None


def validate_recipe(recipe):
    from api import gallery_store
    if not recipe or recipe.get('version') != 1 or recipe.get('hash') != digest({k:v for k,v in recipe.items() if k != 'hash'}):
        raise ValueError('STRUCTURED_INPUTS_UNAVAILABLE')
    if recipe.get('fonts') != font_manifest() or len(recipe.get('fonts',{}))!=4:
        raise ValueError('MANAGED_FONTS_UNAVAILABLE_OR_CHANGED')
    brief = PosterBrief.model_validate(recipe['brief'])
    PosterDesignSpec.model_validate(recipe['spec'])
    if brief.logo_url and not brief.logo_url.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,','data:image/svg+xml;base64,')):
        raise ValueError('MANAGED_LOGO_UNAVAILABLE')
    bg = recipe['background']; payload = gallery_store.load(bg['storage_key'])
    if not payload or hashlib.sha256(payload).hexdigest() != bg['sha256'] or len(payload) != bg['bytes']:
        raise ValueError('BACKGROUND_UNAVAILABLE_OR_CHANGED')
    return payload


def evaluate(brief, contract, *, attempt_id='', payload=b''):
    assessment = assess_copy(brief=brief, contract=contract, attempt_id=attempt_id, payload=payload)
    concept = SimpleNamespace(audience=contract.target, core_benefit=contract.consumer_value,
                              single_message=contract.primary_message)
    strategy = PosterStrategyVerifier().verify(contract=contract, concept=concept, brief=brief,
        product_context=contract.product_offer_focus, language=contract.language).model_dump(mode='json')
    # The general verifier checks concept drift, not every edited field's role. Here only
    # approved consumer message/proof text (or unchanged ancillary text) may occupy copy slots.
    roles = set(contract.consumer_value_claim_ids + contract.reason_to_believe_claim_ids + contract.differentiation_claim_ids)
    allowed = {c.statement for c in contract.allowed_claims if c.claim_id in roles and c.claim_id not in contract.internal_supporting_claim_ids}
    for field in ('headline', 'subheadline'):
        text = getattr(brief, field) or ''
        if text and text not in allowed:
            strategy['defects'].append({'code':'COPY_OUTSIDE_APPROVED_MESSAGE','field':field,
                'detail':'This wording needs strategy review; an exact source match alone does not authorize the message.', 'repair_scope':'COPY'})
    strategy['overall_pass'] = not strategy['defects']
    return assessment.model_dump(mode='json'), strategy


def source_options(contract):
    options = []
    for c in contract.allowed_claims:
        # Same evaluator used on save: a label never grants support.
        brief = SimpleNamespace(business_name='Source options',headline=c.statement,subheadline='',cta_text=contract.cta_direction,offerings=[])
        a, s = evaluate(brief, contract)
        p = next((p for p in a['propositions'] if p['location']=='headline'), None)
        j = next((j for j in a['judgments'] if p and j['proposition_id']==p['proposition_id']), {})
        if j.get('verdict') == 'SUPPORTED':
            options.append({'text':c.statement, 'evidence':j['evidence'],
                'strategy_compatible':not any(d['field']=='headline' for d in s['defects'])})
    return options


def offline_html(brief, background, spec, inputs):
    from poster.template import render_poster_html
    html = render_poster_html(brief, str(background), spec=spec, **inputs)
    html = re.sub(r'<link\b[^>]*>', '', html)
    fonts = Path(__file__).with_name('fonts')
    rules = []
    for family, stem in [('Editor Latin','NotoSans'), ('Editor Arabic','NotoSansArabic')]:
        for weight, suffix in [(400,'Regular'),(700,'Bold')]:
            raw = (fonts/f'{stem}-{suffix}.ttf').read_bytes()
            rules.append("@font-face{font-family:'%s';font-weight:%s;src:url(data:font/ttf;base64,%s)}" % (family,weight,base64.b64encode(raw).decode()))
    rules.append("*{font-family:'Editor Arabic','Editor Latin',sans-serif!important}")
    return html.replace('</head>', '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; font-src data:; style-src \'unsafe-inline\'; script-src \'none\'">'+'<style>'+''.join(rules)+'</style></head>')


def render(recipe, edit):
    from poster.render_playwright import render_html_to_png, inspect_layout
    from poster.pipeline import _repair_local_spec, _copy_zones
    from poster.contrast_gate import check_copy_contrast, ink_for_background
    bg = validate_recipe(recipe)
    brief = PosterBrief.model_validate({**recipe['brief'], **edit.model_dump()})
    spec = PosterDesignSpec.model_validate(recipe['spec'])
    with tempfile.TemporaryDirectory(prefix='adzy-copy-') as directory:
        background = Path(directory)/'background.png'; background.write_bytes(bg)
        inputs = dict(recipe.get('inputs') or {})
        inputs['copy_ink'] = inputs.get('copy_ink') or ink_for_background(str(background), zones=_copy_zones(spec))
        history = []
        for iteration in range(2):
            if spec.copy_surface == 'light': inputs['copy_ink'] = '#11151b'
            elif spec.copy_surface == 'dark': inputs['copy_ink'] = '#f5f3ef'
            html = offline_html(brief, background, spec, inputs)
            path = render_html_to_png(html, Path(directory)/'poster.png', offline=True)
            checks = inspect_layout(html, offline=True)
            contrast = check_copy_contrast(str(path), zones=_copy_zones(spec), ink=inputs['copy_ink'])
            checks['contrast'] = contrast.model_dump(mode='json') if hasattr(contrast,'model_dump') else vars(contrast)
            if not contrast.checked or contrast.passed is not True: checks['defects'].append('LOCAL_CONTRAST')
            history.append(checks)
            if not checks['defects'] or iteration: break
            spec = _repair_local_spec(spec, checks)
        revised = {**recipe, 'brief':brief.model_dump(mode='json'), 'spec':spec.model_dump(mode='json'), 'inputs':inputs}
        revised['hash'] = digest({k:v for k,v in revised.items() if k!='hash'})
        return path.read_bytes(), revised, {**checks, 'repair_history':history}
