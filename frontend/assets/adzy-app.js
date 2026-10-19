(function () {
  'use strict';

  const truth = window.AdzyStrategyState;

  const A = window.Adzy = {
    brands: [],
    brand: null,
    history: [],
    showcase: [],
    intel: null,
    contract: null, review: null, reviewError: '',
    creative: null,
    evidence: [],
    lens: 'direction',
    poll: null,
    introDone: false,
    selectionEpoch: 0, loadingBrand: false, reviewedSwot: null, reviewedStrategy: null,
    contractError: '', busy: false, latestAttempt: null,
    initialLocation: new URLSearchParams(window.location.hash.slice(1)),
  };

  let bootstrapFlight = null;
  let initialStageRestored = false;

  const $ = (id) => document.getElementById(id);
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const text = (value, fallback = '') => {
    if (value === null || value === undefined) return fallback;
    if (typeof value === 'object' && !Array.isArray(value)) {
      if (Object.prototype.hasOwnProperty.call(value, 'value')) return text(value.value, fallback);
      if (Object.prototype.hasOwnProperty.call(value, 'statement')) return text(value.statement, fallback);
    }
    const out = String(value).trim();
    return out || fallback;
  };
  const esc = (value) => text(value).replace(/[&<>'"]/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[ch]));
  const list = (value) => Array.isArray(value) ? value.filter(Boolean) : [];
  const sentence = (value, fallback = 'Still unclear') => text(value, fallback);
  const spark = () => '<span class="adzy-spark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M16 1.75C17.15 10.11 21.89 14.85 30.25 16C21.89 17.15 17.15 21.89 16 30.25C14.85 21.89 10.11 17.15 1.75 16C10.11 14.85 14.85 10.11 16 1.75Z" fill="currentColor"/></svg></span>';
  const safeUrl = (value) => {
    const raw = text(value);
    if (/^\/assets\/[a-z0-9_./-]+$/i.test(raw)) return raw;
    try {
      const url = new URL(raw);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_) { return ''; }
  };

  function brandName(brand = A.brand) {
    return text(brand && (brand.organization_name || brand.name || brand.meta_title), 'Brand');
  }

  function profileOf(brand = A.brand) {
    return (brand && brand._profile) || {};
  }

  function logoOf(brand = A.brand) {
    if (!brand) return '';
    const profile = profileOf(brand);
    const visual = profile.visual || {};
    return safeUrl(brand.logo_url || list(brand.logo_urls)[0] || visual.logo_url || visual.primary_logo);
  }

  function colorsOf(brand = A.brand) {
    if (!brand) return [];
    const profile = profileOf(brand);
    const visual = profile.visual || {};
    return list(brand.brand_colors || visual.palette_hex || profile.brand_colors)
      .map(text).filter((c) => /^#[0-9a-f]{6}$/i.test(c));
  }

  function accentOf(brand = A.brand) {
    return colorsOf(brand)[0] || '#e46c0c';
  }

  function evidenceFrom(value) {
    if (!value || typeof value !== 'object') return [];
    return list(value.evidence || value.sources || value.source_records);
  }

  function firstEvidenceFor(fieldName, fallbackValue = null) {
    const profile = profileOf();
    const own = profile[fieldName];
    const rows = evidenceFrom(own);
    if (rows.length) return normalizeEvidence(rows[0], text(own));
    if (fallbackValue && typeof fallbackValue === 'object') {
      const direct = evidenceFrom(fallbackValue);
      if (direct.length) return normalizeEvidence(direct[0], text(fallbackValue));
    }
    return null;
  }

  function normalizeEvidence(row, value = '') {
    row = row || {};
    return {
      source: text(row.source || row.source_type || row.source_class, 'Website evidence'),
      url: safeUrl(row.page_url || row.source_url || row.url || row.source_ref),
      excerpt: text(row.quote || row.excerpt || row.raw_value || value, value || 'No excerpt returned.'),
      captured: text(row.captured_at || row.observed_at || row.timestamp, 'Capture details unavailable'),
      limitation: list(row.limitations).map(text).filter(Boolean).join(' · ') || text(row.limitation, 'No additional limitation was supplied.'),
      raw: row,
    };
  }

  function evidenceButton(evidence, indexLabel) {
    if (!evidence || (!evidence.url && !evidence.excerpt)) return '<span class="adzy-footnote">Source unavailable</span>';
    const index = A.evidence.push(evidence) - 1;
    const source = evidence.url ? new URL(evidence.url).hostname.replace(/^www\./, '') : evidence.source;
    return `<button class="adzy-footnote" type="button" onclick="adzyOpenEvidence(${index})">${String(indexLabel).padStart(2, '0')} / ${esc(source)}</button>`;
  }

  function claimMap() {
    const candidate = (A.brand && A.brand.strategy_candidate) || {};
    return new Map(list(candidate.claims).map((claim) => [text(claim.claim_id), claim]));
  }

  function evidenceMap() {
    const candidate = (A.brand && A.brand.strategy_candidate) || {};
    return new Map(list(candidate.evidence).map((row) => [text(row.evidence_id), row]));
  }

  async function call(method, path, body) {
    if (typeof window.api !== 'function') throw new Error('The application API is not ready.');
    return window.api(method, path, body);
  }

  function notify(message, type = '') {
    if (typeof window.toast === 'function') window.toast(message, type);
  }

  function setActiveStage(stage) {
    A.stage = stage;
    if (A.brand) window.history.replaceState(null, '', '#' + new URLSearchParams({brand: A.brand.domain_key, stage}));
    all('[data-adzy-stage]').forEach((section) => section.classList.toggle('is-active', section.dataset.adzyStage === stage));
    const navStage = stage === 'strategy' ? 'think' : stage === 'scan' ? 'discover' : stage;
    all('[data-adzy-nav]').forEach((button) => {
      if (button.dataset.adzyNav === navStage) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }

  window.adzyNavigate = async function (stage) {
    initialStageRestored = true;
    if (['think', 'strategy', 'create'].includes(stage) && !A.brand) {
      notify('Choose a brand first.', 'error');
      setActiveStage('discover');
      $('adzy-brand-select').focus();
      return;
    }
    setActiveStage(stage);
    if (stage === 'think') await renderIntelligence();
    if (stage === 'strategy') renderStrategy();
    if (stage === 'create') await renderCreative();
    if (stage === 'work') renderWork();
  };

  function finishIntro() {
    if (A.introDone) return;
    A.introDone = true;
    const logo = $('adzy-intro-logo');
    const intro = $('adzy-intro');
    const slot = $('adzy-logo-slot');
    const fallback = $('adzy-logo-fallback');
    if (!logo || !intro || !slot) return;
    logo.classList.add('is-docking');
    window.setTimeout(() => {
      logo.classList.remove('is-docking');
      logo.classList.add('is-docked');
      logo.onclick = () => window.adzyNavigate('discover');
      logo.setAttribute('aria-label', 'Adzy home');
      if (fallback) fallback.hidden = true;
      slot.prepend(logo);
      intro.classList.add('is-finished');
    }, 760);
  }

  function setupIntro() {
    $('adzy-intro-logo').addEventListener('click', finishIntro, { once: true });
    window.setTimeout(finishIntro, matchMedia('(prefers-reduced-motion: reduce)').matches ? 120 : 2400);
  }

  function setupTopbar() {
    window.addEventListener('scroll', () => $('adzy-topbar').classList.toggle('is-scrolled', window.scrollY > 12), { passive: true });
    $('adzy-brand-select').addEventListener('change', (event) => selectBrand(event.target.value));
    $('adzy-url-form').addEventListener('submit', (event) => {
      event.preventDefault();
      startScrape();
    });
    all('[data-adzy-lens]').forEach((button) => button.addEventListener('click', () => {
      A.lens = button.dataset.adzyLens;
      all('[data-adzy-lens]').forEach((item) => item.classList.toggle('is-active', item === button));
      renderLens();
    }));
    document.addEventListener('keydown', (event) => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.adzy-work-item')) { event.preventDefault(); event.target.click(); return; }
      if (event.key !== 'Escape') return;
      window.adzyCloseDrawer();
      window.adzyCloseTrace();
    });
  }

  async function refreshBrands() {
    if (!window.adzyLegacyState || !window.adzyLegacyState.authToken) return;
    const payload = await call('GET', '/api/brands');
    A.brands = list(payload.brands);
    window.adzyLegacyState.allBrands = A.brands;
    renderBrandOptions();
    renderBrandStrips();
    if (!A.brand && A.brands.length) {
      const preferred = A.brands.find((brand) => brand.domain_key === A.initialLocation.get('brand')) || A.brands[0];
      await selectBrand(preferred.domain_key, false);
    } else if (A.brand) {
      const current = A.brands.find((brand) => brand.domain_key === A.brand.domain_key);
      if (current) await selectBrand(current.domain_key, false);
    }
  }

  async function refreshHistory() {
    if (!window.adzyLegacyState || !window.adzyLegacyState.authToken) return;
    try {
      A.history = list((await call('GET', '/api/history?limit=12')).history);
    } catch (_) { A.history = []; }
    try {
      A.showcase = list((await fetch('/api/showcase').then((response) => response.ok ? response.json() : { images: [] })).images);
    } catch (_) { A.showcase = []; }
    renderParade();
    renderHeroPoster();
    renderBrandStrips();
    renderWork();
    if (A.brand) {
      const saved = bestCreativeFor(A.brand);
      if (saved) renderPoster(saved);
      let selected;
      try { selected = window.localStorage.getItem(revisionStorageKey()); } catch (_) {}
      if (A.latestAttempt) projectAttempt(A.latestAttempt);
      const inspected = A.history.find(item => item.generation_run_id === selected);
      if (inspected) { renderPoster(inspected); rememberRevision(inspected); }
    }
  }

  window.adzyRefresh = async function () {
    try {
      await Promise.all([refreshBrands(), refreshHistory()]);
      notify('Adzy is up to date.', 'success');
    } catch (error) { notify(error.message || String(error), 'error'); }
  };

  function renderBrandOptions() {
    const select = $('adzy-brand-select');
    select.innerHTML = A.brands.length ? '' : '<option value="">No brands yet</option>';
    A.brands.forEach((brand) => {
      const option = document.createElement('option');
      option.value = brand.domain_key;
      option.textContent = brandName(brand);
      option.selected = !!A.brand && A.brand.domain_key === brand.domain_key;
      select.appendChild(option);
    });
  }

  async function selectBrand(domainKey, navigate = true) {
    if (!domainKey) return;
    const epoch = ++A.selectionEpoch;
    try {
      A.loadingBrand = true; A.latestAttempt = null;
      A.reviewedSwot = null; A.reviewedStrategy = null; A.contract = null; A.contractError = ''; A.review = null; A.reviewError = '';
      $('adzy-research').innerHTML = '<p role="status">Loading brand evidence...</p>';
      $('adzy-directions').innerHTML = '';
      $('adzy-brief-rows').innerHTML = '<p role="status">Loading brand...</p>';
      $('adzy-make-btn').disabled = true;
      $('adzy-generation-error').hidden = true;
      resetPoster();
      const brand = await call('GET', `/api/brands/${encodeURIComponent(domainKey)}`);
      if (epoch !== A.selectionEpoch) return;
      A.loadingBrand = false;
      A.brand = brand;
      A.intel = null;
      A.contract = null;
      A.creative = bestCreativeFor(brand);
      window.adzyLegacyState.currentBrand = brand;
      window.adzyLegacyState.studioBrand = brand;
      $('adzy-brand-select').value = brand.domain_key;
      const accent = accentOf(brand);
      document.documentElement.style.setProperty('--brand-accent', accent);
      updateBrandSurfaces();
      renderHeroPoster();
      renderBrandStrips();
      renderResearch();
      const subject = (brand.strategy_candidate || {}).run_id;
      if (subject) {
        try {
          const review = await call('POST', '/api/review/start', {domain_key: domainKey, subject_id: subject});
          if (epoch !== A.selectionEpoch) return;
          A.review = review;
        } catch (error) {
          if (epoch !== A.selectionEpoch) return;
          A.reviewError = error.status === 409 ? 'Strategy changed. Refresh and review the current version.' : errorMessage(error);
        }
      }
      if (epoch !== A.selectionEpoch) return;
      renderStrategy();
      renderWork();
      try { await restorePosterAttempt(epoch); } catch (error) {
        if (epoch === A.selectionEpoch) { $('adzy-generation-error').hidden = false; $('adzy-generation-error').textContent = 'Attempt status unavailable. Check status before generating.'; }
      }
      if (navigate) await window.adzyNavigate('think');
    } catch (error) { if (epoch !== A.selectionEpoch) return; A.brand = null; A.loadingBrand = false; A.reviewedSwot = null; A.reviewedStrategy = null; A.contract = null; notify(`Could not open the brand: ${error.message}`, 'error'); }
  }

  window.adzySelectBrand = selectBrand;

  function updateBrandSurfaces() {
    if (!A.brand) return;
    const name = brandName();
    const provenance = profileOf().demo_provenance;
    const disclosure = $('adzy-demo-disclosure');
    if (disclosure) {
      disclosure.hidden = !provenance;
      disclosure.textContent = provenance ? 'Public-source portfolio demonstration · AI-curated evidence · Human approvals are recorded separately. Not affiliated with the featured brand.' : '';
    }
    const tagline = text(A.brand.tagline || A.brand.meta_description || A.brand.description, 'We found the signals that shape this brand.');
    $('adzy-think-brand').textContent = name;
    $('adzy-think-tagline').textContent = tagline;
    $('adzy-next-brand').textContent = name;
    $('adzy-strategy-brand').textContent = name;
    const logo = logoOf();
    const logoWrap = $('adzy-think-logo');
    logoWrap.innerHTML = logo ? `<img src="${esc(logo)}" alt="${esc(name)} logo">` : `<span>${esc(name[0] || 'B')}</span>`;
    const moodLogo = $('adzy-mood-logo');
    moodLogo.innerHTML = logo ? `<img src="${esc(logo)}" alt="${esc(name)} logo">` : `<span class="adzy-display">${esc(name)}</span>`;
    $('adzy-avatar').textContent = text(window.adzyLegacyState.currentUser && (window.adzyLegacyState.currentUser.full_name || window.adzyLegacyState.currentUser.email), 'A')[0].toUpperCase();
  }

  function creativeImage(item) {
    if (item && item.image_b64) return `data:image/png;base64,${item.image_b64}`;
    return safeUrl(item && (item.image_url || item.url));
  }

  function creatives() {
    const fromHistory = A.history.filter((item) => creativeImage(item));
    const fromShowcase = A.showcase.map((url, index) => ({ image_url: url, filename: `Adzy work ${index + 1}`, brand_name: 'Adzy', canonical: false }));
    return [...fromHistory, ...fromShowcase];
  }

  function curatedCreatives(limit = 7) {
    const seen = new Set();
    return creatives().filter((item) => {
      const key = creativeImage(item);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).sort((a, b) => Number(!!(b.canonical && b.strategy_grounded)) - Number(!!(a.canonical && a.strategy_grounded)))
      .slice(0, limit);
  }

  function bestCreativeFor(brand = A.brand) {
    if (!brand) return null;
    let selected;
    try {
      const owner = (window.adzyLegacyState.currentUser || {}).id;
      selected = window.localStorage.getItem(`adzy.poster.operation:${owner}:${brand.domain_key}:selected-revision`);
    } catch (_) {}
    return A.history.find(item => item.domain_key === brand.domain_key && item.generation_run_id === selected)
      || A.history.find((item) => item.domain_key === brand.domain_key && item.canonical)
      || A.history.find((item) => item.domain_key === brand.domain_key)
      || null;
  }

  function renderHeroPoster() {
    const creative = A.brand ? bestCreativeFor() : creatives()[0];
    const wrap = $('adzy-hero-poster');
    if (creative && creativeImage(creative)) {
      wrap.innerHTML = `<img src="${creativeImage(creative)}" alt="${esc(brandName(A.brand) || creative.brand_name || 'Adzy')} campaign poster">`;
      const contract = creative.creative_contract || {};
      $('adzy-hero-evidence').textContent = text(contract.primary_message || (creative.why_this_poster || {}).message, 'Saved creative · inspect its checks before delivery.');
    } else {
      wrap.innerHTML = `<div class="adzy-hero-awaiting"><span>YOUR NEXT CREATIVE</span><strong>${esc(A.brand ? brandName() : 'Find the angle.')}</strong><p>Review the strategy, then create this brand’s first poster.</p></div>`;
      $('adzy-hero-evidence').textContent = 'No poster has been generated for this brand yet.';
    }
  }

  function renderParade() {
    const root = $('adzy-parade');
    const items = curatedCreatives();
    if (!items.length) {
      root.innerHTML = '<div class="adzy-empty" style="color:var(--adzy-vanilla);border-color:var(--adzy-vanilla);">No poster imagery has been returned yet.</div>';
      return;
    }
    const colors = ['#5265ff', '#ffc93d', '#ff9dc8', '#63ddb2', '#aa8bff', '#ff665a'];
    const card = (item, index) => `<figure class="adzy-parade-card" style="--campaign:${colors[index % colors.length]}" tabindex="0"><img src="${creativeImage(item)}" loading="lazy" alt="${esc(item.brand_name || 'Adzy creative')}"><figcaption><strong>${esc((item.brand_name || 'ADZY').toUpperCase())} / ${String(index + 1).padStart(3, '0')}</strong><span>${item.canonical && item.strategy_grounded ? 'STRATEGY GROUNDED' : 'ADZY CREATIVE'}</span></figcaption></figure>`;
    root.innerHTML = [0, 1].map((lane) => {
      const shifted = items.map((_, i) => items[(i + lane) % items.length]);
      const doubled = [...shifted, ...shifted];
      return `<div class="adzy-lane ${lane === 1 ? 'reverse' : ''}">${doubled.map((item, i) => card(item, i + lane)).join('')}</div>`;
    }).join('');
  }

  function brandStatus(brand) {
    const creative = bestCreativeFor(brand);
    if (creative) return ['Poster ready', creative.canonical ? 'Strategy grounded' : 'Creative saved'];
    if (brand.strategy_approval_state === 'approved' && brand.strategy_version_id) return ['Strategy locked', 'Ready to create'];
    if (brand.strategy_candidate && brand.strategy_candidate.run_id) return ['Angle found', 'Waiting for approval'];
    if (brand.has_swot) return ['Still thinking', 'Research collected'];
    return ['Still researching', 'Discovery stage'];
  }

  function renderBrandStrips() {
    const root = $('adzy-brand-strips');
    if (!A.brands.length) {
      root.innerHTML = '<div class="adzy-empty">No brands yet. Drop a website above to begin.</div>';
      return;
    }
    root.innerHTML = A.brands.map((brand) => {
      const logo = logoOf(brand);
      const accent = accentOf(brand);
      const status = brandStatus(brand);
      const creative = bestCreativeFor(brand);
      return `<button class="adzy-brand-strip" type="button" style="--strip-accent:${accent}" onclick="adzySelectBrand('${esc(brand.domain_key)}')"><span class="adzy-brand-mark">${logo ? `<img src="${esc(logo)}" alt="">` : esc(brandName(brand)[0])}</span><span><span class="adzy-brand-name">${esc(brandName(brand))}</span><span class="adzy-brand-url">${esc(brand.source_url)}</span></span><span class="adzy-brand-status">${esc(status[0])}<small>${esc(status[1])}</small></span><span class="adzy-strip-creative">${creative ? `<img src="${creativeImage(creative)}" alt="">` : ''}</span></button>`;
    }).join('');
  }

  function countEvidence(value, seen = new Set()) {
    if (!value || typeof value !== 'object' || seen.has(value)) return 0;
    seen.add(value);
    if (Array.isArray(value)) return value.reduce((sum, item) => sum + countEvidence(item, seen), 0);
    let total = Array.isArray(value.evidence) ? value.evidence.length : 0;
    for (const child of Object.values(value)) total += countEvidence(child, seen);
    return total;
  }

  function countPages(brand) {
    const profile = profileOf(brand);
    const candidates = [brand.pages_count, brand.page_count, profile.pages_count, profile.page_count,
      list(profile.pages).length, list(profile.manifest && profile.manifest.pages).length];
    return Number(candidates.find((value) => Number(value) > 0) || 0);
  }

  async function startScrape() {
    const input = $('adzy-url-input');
    let url = input.value.trim();
    if (!url) { notify('Paste a brand website first.', 'error'); input.focus(); return; }
    if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
    input.value = url;
    setActiveStage('scan');
    $('adzy-scan-url').textContent = url;
    $('adzy-scan-title').innerHTML = 'Looking<br>around...';
    $('adzy-scan-status').textContent = 'Starting with the website';
    $('adzy-scan-metrics').hidden = true;
    $('adzy-scan-error').hidden = true;
    try {
      const started = await call('POST', '/api/scrape', { url, max_pages: 20, max_depth: 3, delay: 1.0 });
      if (A.poll) clearInterval(A.poll);
      A.poll = window.setInterval(() => pollScrape(started.job_id), 1800);
      await pollScrape(started.job_id);
    } catch (error) {
      $('adzy-scan-error').hidden = false;
      $('adzy-scan-error').textContent = error.message || String(error);
      $('adzy-scan-status').textContent = 'We could not start that exploration.';
    }
  }

  async function pollScrape(jobId) {
    try {
      const job = await call('GET', `/api/jobs/${jobId}`);
      $('adzy-scan-status').textContent = text(job.step, 'Exploring the brand');
      if (job.status === 'done') {
        clearInterval(A.poll); A.poll = null;
        const brand = await call('GET', `/api/brands/${encodeURIComponent(job.domain_key)}`);
        A.brand = brand;
        window.adzyLegacyState.currentBrand = brand;
        window.adzyLegacyState.studioBrand = brand;
        const pages = countPages(brand);
        const evidence = countEvidence(profileOf(brand));
        const peers = list(brand.competitors).length || list((brand.strategy_candidate || {}).evidence).filter((row) => row.source_class === 'COMPETITOR_ANALYSIS').length;
        $('adzy-pages-count').textContent = pages;
        $('adzy-evidence-count').textContent = evidence;
        $('adzy-peer-count').textContent = peers;
        $('adzy-scan-metrics').hidden = false;
        $('adzy-scan-title').innerHTML = 'Okay.<br>We see it now.';
        $('adzy-scan-status').textContent = job.warning || 'Real returned data is ready.';
        await Promise.all([refreshBrands(), refreshHistory()]);
        window.setTimeout(() => window.adzyNavigate('think'), 1350);
      } else if (job.status === 'error') {
        clearInterval(A.poll); A.poll = null;
        $('adzy-scan-error').hidden = false;
        $('adzy-scan-error').textContent = text(job.error, 'The exploration could not finish.');
      }
    } catch (_) { /* keep the durable job alive through a transient poll failure */ }
  }

  async function renderIntelligence() {
    if (!A.brand) return;
    updateBrandSurfaces();
    const board = $('adzy-discovery-board');
    board.innerHTML = '<div class="adzy-empty" style="grid-column:1/-1;">Looking through the stored evidence...</div>';
    const epoch = A.selectionEpoch;
    try {
      const intel = await call('GET', `/api/intel/${encodeURIComponent(A.brand.domain_key)}`);
      if (epoch !== A.selectionEpoch) return;
      A.intel = intel;
    } catch (_) { if (epoch !== A.selectionEpoch) return; A.intel = null; }
    renderResearch();
    A.evidence = [];
    const profile = profileOf();
    const candidate = A.brand.strategy_candidate || {};
    const ownItems = (A.intel && A.intel.own && A.intel.own.items) || {};
    const pain = list(ownItems.pain_points)[0];
    const testimonial = list(A.brand.testimonials || profile.testimonials)[0];
    const canonicalVoice = truth.findCustomerVoice(candidate);
    const customerValue = text(pain && pain.value) || text(testimonial)
      || text(canonicalVoice && (canonicalVoice.evidence.excerpt || canonicalVoice.evidence.raw_value))
      || 'No customer voice was returned.';
    const customerEvidence = pain ? normalizeEvidence(pain, customerValue)
      : firstEvidenceFor('testimonials', testimonial)
        || (canonicalVoice ? normalizeEvidence(canonicalVoice.evidence, customerValue) : null);
    const customerLimitation = pain
      ? 'One surfaced signal — do not generalize beyond the source.'
      : testimonial
        ? 'On-site testimonial — not independent customer prevalence.'
        : canonicalVoice
          ? list(canonicalVoice.claim.limitations).map(text).filter(Boolean).join(' · ') || 'Canonical customer signal — inspect its source and scope.'
          : 'No quotable customer source returned.';
    const capabilityRaw = list(profile.value_propositions)[0] || list(A.brand.key_messages)[0] || list(ownItems.usps)[0];
    const capability = text(capabilityRaw, 'No supported capability was returned.');
    const capabilityEvidence = (capabilityRaw && capabilityRaw.source_url) ? normalizeEvidence(capabilityRaw, capability) : firstEvidenceFor('value_propositions', capabilityRaw) || firstEvidenceFor('description');
    const decision = candidate.decision || {};
    const selected = list(decision.options).find((option) => option.option_id === decision.selected_option_id) || list(decision.options)[0];
    const peers = list(A.intel && A.intel.peers);
    const peerCount = peers.length || list(A.brand.competitors).length || list(candidate.evidence).filter((row) => row.source_class === 'COMPETITOR_ANALYSIS').length;
    const unknowns = [...list(decision.unresolved_unknowns), ...list((candidate.research_question || {}).unknowns)];
    const readiness = profile.readiness || {};
    const missing = [...list(readiness.missing_critical), ...list(readiness.missing_recommended), ...list(readiness.swot_blockers), ...list(readiness.strategy_blockers)];
    const unknown = text(unknowns[0] || missing[0], 'No unresolved unknown was returned.');
    const inference = text(selected && (selected.consumer_value || selected.differentiated_value || selected.positioning_statement), text((A.brand.marketing_strategy || {}).value_proposition, 'No strategy inference is approved yet.'));
    const peerEvidence = list(candidate.evidence).find((row) => row.source_class === 'COMPETITOR_ANALYSIS');
    const inferredEvidence = selected && list(selected.supporting_claim_ids).length ? claimMap().get(text(selected.supporting_claim_ids[0])) : null;

    board.innerHTML = `
      <article class="adzy-tile customer"><div class="adzy-tile-label"><i class="adzy-dot" style="--dot:${customerEvidence ? 'var(--adzy-mint)' : 'transparent'}"></i>Customer voice · observation</div><span class="adzy-board-mark" aria-hidden="true">“</span><h2 dir="auto">${customerEvidence ? `“${esc(customerValue)}”` : esc(customerValue)}</h2><p class="adzy-tile-copy">${customerEvidence ? 'A returned signal, kept at the strength the evidence supports.' : 'This evidence class stays visibly unresolved.'}</p><footer class="adzy-tile-foot">${evidenceButton(customerEvidence, 1)}<span class="adzy-limitation">${esc(customerLimitation)}</span></footer></article>
      <article class="adzy-tile capability"><div class="adzy-tile-label"><i class="adzy-dot" style="--dot:var(--adzy-mint)"></i>Brand capability</div><h2>${esc(capability)}</h2><p class="adzy-tile-copy">A capability carried by the current brand profile.</p><footer class="adzy-tile-foot">${evidenceButton(capabilityEvidence, 2)}</footer></article>
      <article class="adzy-tile competition"><div class="adzy-tile-label"><i class="adzy-dot" style="--dot:var(--adzy-mint)"></i>Competitive set</div><p class="adzy-tile-number">${peerCount}</p><h2>${peerCount === 1 ? 'peer compared' : 'peers compared'}</h2><footer class="adzy-tile-foot">${peerEvidence ? evidenceButton(normalizeEvidence(peerEvidence), 3) : '<span class="adzy-footnote">No competitor source returned</span>'}</footer></article>
      <article class="adzy-tile insight"><div class="adzy-tile-label"><i class="adzy-dot" style="--dot:var(--adzy-blue)"></i>What we learned · inference</div><span class="adzy-annotation-line" aria-hidden="true"></span><h2>${esc(inference)}</h2><p class="adzy-tile-copy">An interpretation for the strategy — not an observed customer fact.</p><footer class="adzy-tile-foot">${inferredEvidence ? evidenceButton(claimToEvidence(inferredEvidence), 4) : '<span class="adzy-footnote">Strategy link not yet available</span>'}</footer></article>
      <article class="adzy-tile unknown"><div class="adzy-unknown-mark">?</div><div class="adzy-tile-label"><i class="adzy-dot"></i>Still unclear</div><h2>${esc(unknown)}</h2><p class="adzy-tile-copy">An explicit gap stays visible instead of becoming invented certainty.</p></article>`;
  }

  function claimToEvidence(claim) {
    if (!claim) return null;
    const map = evidenceMap();
    const evidence = map.get(text(list(claim.supporting_evidence_ids)[0]));
    return evidence ? normalizeEvidence(evidence, claim.statement) : {
      source: 'Strategy claim', url: '', excerpt: text(claim.statement), captured: 'Current approved strategy', limitation: list(claim.limitations).map(text).join(' · ') || 'Claim-level source details are unavailable in this view.', raw: claim,
    };
  }

  window.adzyOpenEvidence = function (index) {
    const evidence = A.evidence[index];
    if (!evidence) return;
    const content = $('adzy-drawer-content');
    content.innerHTML = `<section class="adzy-drawer-section"><h3>Source</h3>${evidence.url ? `<p><a href="${esc(evidence.url)}" target="_blank" rel="noopener noreferrer">${esc(evidence.url)}</a></p>` : `<p>${esc(evidence.source)}</p>`}</section><section class="adzy-drawer-section"><h3>Excerpt</h3><p>“${esc(evidence.excerpt)}”</p></section><section class="adzy-drawer-section"><h3>Captured</h3><p>${esc(evidence.captured)}</p></section><section class="adzy-drawer-section"><h3>Limitation</h3><p>${esc(evidence.limitation)}</p></section><details><summary>View raw</summary><pre>${esc(JSON.stringify(evidence.raw || {}, null, 2))}</pre></details>`;
    $('adzy-drawer').classList.add('is-open');
    $('adzy-drawer').setAttribute('aria-hidden', 'false');
    $('adzy-drawer-backdrop').classList.add('is-open');
    $('adzy-drawer').querySelector('.adzy-drawer-close').focus();
  };

  window.adzyCloseDrawer = function () {
    $('adzy-drawer').classList.remove('is-open');
    $('adzy-drawer').setAttribute('aria-hidden', 'true');
    $('adzy-drawer-backdrop').classList.remove('is-open');
  };

  function errorMessage(error) {
    try {
      const detail = JSON.parse(error.message);
      return [detail.message || detail.code, detail.safe_alternative].filter(Boolean).join(' ');
    } catch (_) { return error.message || String(error); }
  }

  function renderResearch() {
    const root = $('adzy-research');
    A.reviewedSwot = null;
    if (!A.brand || A.loadingBrand) { root.innerHTML = '<p>Choose a brand to review its evidence.</p>'; return; }
    const b = A.brand, swot = b.swot || {};
    const summary = [swot.summary, ...list(swot.notes)].filter(Boolean).map(v => `<p>${esc(v)}</p>`).join('');
    const quadrants = ['strengths', 'weaknesses', 'opportunities', 'threats', 'site_readiness'].map(key => {
      const items = list(swot[key]);
      return `<section><h3>${esc(key.replace(/_/g, ' '))}</h3>${items.length ? '<ul>' + items.map(item => {
        const citations = list(item.citations || item.citation || item.sources || (item.source_url ? [item.source_url] : []));
        return `<li><strong>${esc(item.title || item.text || item.statement)}</strong><p>${esc(item.reason || item.reasoning || item.evidence || '')}</p>${citations.map(c => {
          const url = safeUrl(typeof c === 'string' ? c : c.page_url || c.url);
          return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>` : `<span>${esc(text(c))}</span>`;
        }).join(' ')}</li>`;
      }).join('') + '</ul>' : '<p>No findings stored in this section.</p>'}</section>`;
    }).join('');
    root.innerHTML = `<h2 id="adzy-research-title">Research &amp; SWOT review</h2><p role="status">${A.busy ? 'Analysis in progress. Please wait for the persisted result.' : b.has_swot ? b.swot_approved ? 'This SWOT version is approved.' : 'Review this SWOT version before approving.' : 'Next: generate SWOT from the stored evidence.'}</p>`
      + (b.has_swot ? `<p class="adzy-mono">Reviewed version: ${esc(b.swot_run_id || 'Legacy — immutable Run identity unavailable')}</p>${summary}<details><summary>Review SWOT, sources &amp; readiness</summary><div class="adzy-research-grid">${quadrants}</div></details>` : '')
      + `<div class="adzy-review-actions"><button class="adzy-btn flat" type="button" onclick="adzyGenerateSwot()" ${A.busy ? 'disabled' : ''}>${b.has_swot ? 'Regenerate SWOT' : 'Generate SWOT'}</button>`
      + (b.has_swot && !b.swot_approved ? `<button id="adzy-approve-swot" class="adzy-btn coral" type="button" onclick="adzyApproveSwot()" ${A.busy ? 'disabled' : ''}>Approve this SWOT version</button>` : '')
      + (b.swot_approved ? '<button class="adzy-btn ink" type="button" onclick="adzyNavigate(\'strategy\')">Review strategy →</button>' : '') + '</div>';
    if (b.has_swot) A.reviewedSwot = Object.freeze({domain: b.domain_key, id: b.swot_run_id || null, epoch: A.selectionEpoch});
  }

  async function runAnalysis(kind, payload) {
    if (!A.brand || A.loadingBrand || A.busy) return;
    const domain = A.brand.domain_key, epoch = A.selectionEpoch;
    A.busy = true; renderResearch();
    try {
      const started = await call('POST', `/api/${kind}/generate`, {domain_key: domain, ...payload});
      await pollAnalysisJob(started.job_id, kind, domain, epoch);
    } catch (error) { notify(errorMessage(error) + ' Review the evidence and retry when ready.', 'error'); }
    finally { A.busy = false; if (A.brand && A.brand.domain_key === domain) { renderResearch(); renderApproval(); } }
  }

  window.adzyGenerateSwot = () => runAnalysis('swot', {});
  window.adzyApproveSwot = async function () {
    const reviewed = A.reviewedSwot;
    if (!reviewed || A.loadingBrand || A.busy) return;
    A.busy = true;
    const button = $('adzy-approve-swot'); if (button) button.disabled = true;
    try {
      await call('POST', '/api/swot/approve', {domain_key: reviewed.domain, swot_run_id: reviewed.id});
      if (A.selectionEpoch !== reviewed.epoch) return;
      await selectBrand(reviewed.domain, false);
      notify('The reviewed SWOT version is approved.', 'success');
    } catch (error) {
      if (error.status === 409 && A.selectionEpoch === reviewed.epoch) {
        await selectBrand(reviewed.domain, false);
        notify('SWOT changed. Review the updated version and approve it explicitly.', 'error');
      } else notify(errorMessage(error), 'error');
    } finally { A.busy = false; renderResearch(); }
  };

  function strategyData() {
    const candidate = (A.brand && A.brand.strategy_candidate) || {};
    const decision = candidate.decision || {};
    let options = list(decision.options);
    let selectedId = text(decision.selected_option_id);
    if (!options.length && A.brand && A.brand.marketing_strategy) {
      const strategy = A.brand.marketing_strategy;
      if (text(strategy.positioning_statement || strategy.value_proposition)) {
        options = [{
          option_id: 'approved-projection',
          positioning_statement: text(strategy.positioning_statement),
          target: list(strategy.audience)[0] || '',
          differentiated_value: text(strategy.value_proposition),
          consumer_value: text(strategy.value_proposition),
          reason_to_believe: '', trade_offs: [], supporting_claim_ids: [],
        }];
        selectedId = 'approved-projection';
      }
    }
    return { candidate, decision, options, selectedId, selected: options.find((option) => text(option.option_id) === selectedId) || options[0] || null };
  }

  function renderStrategy() {
    if (!A.brand) return;
    const data = strategyData();
    A.reviewedStrategy = data.candidate.run_id ? Object.freeze({domain: A.brand.domain_key, id: data.candidate.run_id, eligible: !!A.review && A.review.status === 'NEEDS_REVIEW', epoch: A.selectionEpoch}) : null;
    $('adzy-strategy-prerequisite').innerHTML = A.brand.swot_approved
      ? '<p>Current SWOT approved. Review the directions and their evidence before deciding.</p>'
      : '<p>Next: review and approve the current SWOT before generating strategy.</p><button class="adzy-btn coral" type="button" onclick="adzyNavigate(\'think\')">Review research &amp; SWOT</button>';
    const question = data.candidate.research_question || {};
    const objective = data.candidate.objective || {};
    const business = objective.business || objective;
    $('adzy-research-question').textContent = text(question.question, 'No canonical ResearchQuestion has been stored for this brand yet.');
    const objectiveForm = $('adzy-strategy-objective');
    objectiveForm.hidden = false;
    if (business.type) $('adzy-objective-type').value = business.type;
    if (business.description) $('adzy-objective-description').value = business.description;
    if (list(business.geography).length) $('adzy-objective-geography').value = list(business.geography).join(', ');
    const root = $('adzy-directions');
    if (!data.options.length) {
      root.innerHTML = '<div class="adzy-empty" style="grid-column:1/-1;">No canonical Directions exist yet. Adzy will only show options returned by the strategy service.</div>';
    } else {
      const colors = ['var(--adzy-blue)', 'var(--adzy-mango)', 'var(--adzy-grape)'];
      const inks = ['white', 'var(--adzy-ink)', 'var(--adzy-ink)'];
      const duplicateIndexes = truth.duplicateAlternativeIndexes(data.options);
      root.classList.toggle('has-canonical-duplicates', duplicateIndexes.size > 0);
      root.classList.toggle('has-selection', !!data.selectedId);
      const disclosure = duplicateIndexes.size
        ? '<aside class="adzy-canonical-gap"><strong>Canonical review note</strong><span>Some approved options are materially identical. They remain visible as stored; distinct alternatives require a new strategy run and approval.</span></aside>'
        : '';
      root.innerHTML = disclosure + data.options.slice(0, 3).map((option, index) => {
        const selected = text(option.option_id) === data.selectedId;
        const target = friendlyTarget(option.target);
        const duplicateOf = duplicateIndexes.get(index);
        return `<article class="adzy-direction ${selected ? 'is-selected' : ''} ${duplicateOf !== undefined ? 'is-canonical-duplicate' : ''}" style="--direction-color:${colors[index % colors.length]};--direction-ink:${inks[index % inks.length]}"><div class="adzy-direction-num"><span>${String(index + 1).padStart(2, '0')}</span>${selected ? `<span class="adzy-pick-label">Adzy's pick ${spark()}</span>` : ''}</div>${duplicateOf !== undefined ? `<p class="adzy-duplicate-badge">Same territory as ${String(duplicateOf + 1).padStart(2, '0')} · canonical content</p>` : ''}<h3>${esc(option.consumer_value || option.differentiated_value || option.positioning_statement)}</h3><p class="adzy-direction-target"><span>For</span>${esc(target.lead)}</p><p class="adzy-direction-difference"><span>Strategic difference</span>${esc(sentence(option.differentiated_value))}</p><details class="adzy-direction-details"><summary>Reason &amp; trade-off</summary><dl><div><dt>Why believe it</dt><dd>${esc(sentence(option.reason_to_believe))}</dd></div><div><dt>Trade-off</dt><dd>${esc(sentence(list(option.trade_offs)[0], 'No trade-off returned.'))}</dd></div>${target.qualifier ? `<div><dt>Target qualification</dt><dd>${esc(target.qualifier)}</dd></div>` : ''}</dl></details></article>`;
      }).join('');
    }
    renderLens();
    renderApproval();
  }

  function selectedProofs() {
    const { selected } = strategyData();
    if (!selected) return [];
    const claims = claimMap();
    return list(selected.supporting_claim_ids).map((id) => claims.get(text(id))).filter(Boolean);
  }

  function renderLens() {
    const panel = $('adzy-lens-panel');
    const { candidate, decision, selected } = strategyData();
    if (!selected) {
      panel.innerHTML = '<h3>No decision to inspect yet.</h3><p>Proof and watch-outs stay empty until a real strategy candidate exists.</p>';
      return;
    }
    if (A.lens === 'proof') {
      const proofs = selectedProofs();
      const groups = truth.groupEvidenceRoles(proofs, candidate.evidence);
      let sourceNumber = 1;
      panel.innerHTML = `<div class="adzy-lens-heading"><div><p class="adzy-mono">Evidence roles</p><h3>What supports the decision — and how</h3></div><p>These are strategic inputs. Only approved consumer proof may enter the ad.</p></div><div class="adzy-proof-groups">${groups.length ? groups.map((group) => `<section class="adzy-proof-group is-${group.key}"><h4>${esc(group.label)}</h4>${group.items.map(({ claim, evidence }) => { const normalized = evidence ? normalizeEvidence(evidence, claim.statement) : null; const limitation = Array.from(new Set([...list(claim.limitations), ...list(evidence && evidence.limitations)].map(text).filter(Boolean))).join(' · '); return `<article class="adzy-proof-item"><p>${esc(claim.statement)}</p><div class="adzy-proof-meta"><span>${esc(text(claim.epistemic_type, 'Claim').replaceAll('_', ' '))} · strategic use only</span>${normalized ? evidenceButton(normalized, sourceNumber++) : '<span>Source relationship unavailable</span>'}</div>${limitation ? `<small>${esc(limitation)}</small>` : ''}</article>`; }).join('')}</section>`).join('') : '<div class="adzy-empty">No supporting Claim artifacts were returned in this projection.</div>'}</div>`;
      return;
    }
    if (A.lens === 'watchouts') {
      const targetQualifier = friendlyTarget(selected.target).qualifier;
      const tradeoffs = Array.from(new Set([...list(selected.trade_offs), ...(targetQualifier ? [`Target qualification: ${targetQualifier}`] : [])].map(text).filter(Boolean)));
      const unknowns = Array.from(new Set([...list(decision.unresolved_unknowns), ...list((candidate.research_question || {}).unknowns)].map(text).filter(Boolean)));
      const proofGroups = truth.groupEvidenceRoles(selectedProofs(), candidate.evidence);
      const limitations = Array.from(new Set([
        ...list(decision.contradictions),
        ...proofGroups.filter((group) => group.key === 'limitation').flatMap((group) => group.items.flatMap(({ claim, evidence }) => [claim.statement, ...list(claim.limitations), ...list(evidence && evidence.limitations)])),
      ].map(text).filter(Boolean)));
      const watchGroup = (label, items, kind) => `<section class="adzy-watch-group is-${kind}"><p class="adzy-mono">${label}</p>${items.length ? `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p class="adzy-watch-empty">Nothing returned in this category.</p>'}</section>`;
      panel.innerHTML = `<div class="adzy-lens-heading"><div><p class="adzy-mono">Decision boundaries</p><h3>What keeps this direction honest</h3></div><p>Unknowns stay visible; they are not failures dressed up as certainty.</p></div><div class="adzy-watch-grid">${watchGroup('Trade-off', tradeoffs, 'tradeoff')}${watchGroup("What we don't know yet", unknowns, 'unknown')}${watchGroup('Evidence limitations', limitations, 'limitation')}</div>`;
      return;
    }
    const directionLead = selected.consumer_value || selected.differentiated_value || selected.positioning_statement;
    const directionTarget = friendlyTarget(selected.target).lead;
    const directionProof = selected.reason_to_believe || selected.differentiated_value || selected.consumer_value;
    const fullPositioning = text(selected.positioning_statement);
    panel.innerHTML = `<h3>${esc(directionLead)}</h3><p><strong>For:</strong> ${esc(directionTarget)}${text(directionProof) !== text(directionLead) ? ` · ${esc(directionProof)}` : ''}</p><p><strong>Decision rationale:</strong> ${esc(sentence(decision.rationale, 'No rationale text was returned.'))}</p>${fullPositioning && fullPositioning !== directionLead ? `<details class="adzy-raw-detail"><summary>${A.review && A.review.status === 'READY_FOR_CREATIVE' ? 'Full approved positioning' : 'Full positioning proposal'}</summary><p>${esc(fullPositioning)}</p></details>` : ''}`;
  }

  function renderApproval() {
    const root = $('adzy-approval');
    if (!A.brand || A.loadingBrand) { root.innerHTML = '<p>Choose a brand to review its strategy.</p>'; return; }
    const { candidate } = strategyData();
    const verification = candidate.verification || {};
    const approved = !!A.review && A.review.status === 'READY_FOR_CREATIVE';
    const defects = list(verification.defects);
    const limitations = [...list((candidate.decision || {}).unresolved_unknowns), ...list((candidate.research_question || {}).unknowns)];
    const rejected = candidate.approval_state === 'rejected';
    const awaitingSwot = !A.brand.swot_approved && !!candidate.run_id && verification.eligible_for_approval === true && !defects.length;
    const canApprove = !!A.review && A.review.status === 'NEEDS_REVIEW' && !A.busy;
    const duplicateAuditCount = truth.duplicateAlternativeIndexes((candidate.decision || {}).options).size;
    const cta = (approved
      ? '<span class="adzy-locked-pill">Strategy locked <b>✓</b></span><button class="adzy-btn grape" type="button" onclick="adzyNavigate(\'create\')">Prepare the poster</button>'
      : canApprove
        ? '<button class="adzy-btn coral" id="adzy-lock-btn" type="button" onclick="adzyLockStrategy()">Lock this strategy</button>'
        : awaitingSwot ? '<p>The evidence checks passed with disclosed limitations. Review the current SWOT before deciding on this strategy.</p><button class="adzy-btn coral" type="button" onclick="adzyNavigate(\'think\')">Review research &amp; SWOT</button>' : '<p>Not ready to lock. Review the persisted research gaps below.</p>')
      + `<button class="adzy-btn flat" id="adzy-find-angle-btn" type="button" onclick="adzyGenerateStrategy()" ${!A.brand.swot_approved || A.busy ? 'disabled' : ''}>${candidate.run_id ? 'Regenerate directions' : 'Find the directions'}</button>`
      + (canApprove ? '<button class="adzy-btn flat" type="button" onclick="adzyRejectStrategy()">Reject this version</button>' : '')
      + (candidate.approval_state === 'rejected' ? '<p>This strategy version was rejected. Review the objective and evidence, then generate a new version.</p>' : '')
      + (defects.length ? '<details open><summary>Research gaps &amp; recovery</summary><ul>' + defects.map(d => `<li><strong>${esc(d.code)}</strong>: ${esc(d.detail || d.message)} ${esc(d.safe_alternative || '')}</li>`).join('') + '</ul><p>Review sources in Think. Add the missing evidence through the supported brand ingestion, then regenerate; Adzy will recheck sufficiency.</p></details>' : '');
    root.classList.toggle('is-approved', approved);
    root.classList.toggle('is-ready', canApprove);
    root.innerHTML = `<p role="status" id="adzy-workflow-status">${esc(awaitingSwot ? 'Waiting for SWOT review' : A.review ? A.review.status : 'Review unavailable')} ${esc(A.reviewError)} ${esc(A.review ? [...A.review.reasons, ...A.review.required_actions].join(' · ') : '')}</p><div><div class="adzy-approval-check" aria-label="${approved ? 'Approved strategy' : canApprove ? 'Ready for owner decision' : awaitingSwot ? 'SWOT review required' : 'Strategy needs support'}">${approved ? '✓' : canApprove ? '→' : '!'}</div><h2>${approved ? 'This direction holds up.' : rejected ? 'This direction was rejected.' : canApprove ? 'This direction is ready for you.' : awaitingSwot ? 'Review the SWOT first.' : 'The direction needs more support.'}</h2><div class="adzy-verifier-lines"><span><i class="adzy-dot" style="--dot:${verification.status && !defects.length ? 'var(--adzy-mint)' : 'transparent'}"></i>Research · ${esc(text((candidate.research_question || {}).sufficiency_status, 'not assessed'))}</span><span><i class="adzy-dot" style="--dot:${!defects.length && verification.status ? 'var(--adzy-mint)' : 'var(--adzy-watermelon)'}"></i>${defects.length} persisted verifier issue${defects.length === 1 ? '' : 's'}</span>${duplicateAuditCount ? `<span><i class="adzy-dot" style="--dot:var(--adzy-watermelon)"></i>${duplicateAuditCount} current duplicate audit gap</span>` : ''}<span><i class="adzy-dot" style="--dot:var(--adzy-mango)"></i>${limitations.length} limitation${limitations.length === 1 ? '' : 's'} disclosed</span></div></div><div>${cta}</div>`;
  }

  window.adzyGenerateStrategy = async function () {
    const type = $('adzy-objective-type').value;
    const description = $('adzy-objective-description').value.trim();
    const geography = $('adzy-objective-geography').value.split(',').map((part) => part.trim()).filter(Boolean);
    if (!type || !description) { notify('Choose and describe the explicit business objective.', 'error'); return; }
    const unknown = { status: 'UNKNOWN', value: null };
    try {
      await runAnalysis('strategy', {
        objective: {
          schema_version: 1,
          objective_id: globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : `obj_${Date.now()}`,
          origin: 'USER_EXPLICIT',
          business: { type, description, geography },
          behavioral_objective: unknown, metric: unknown, baseline: unknown, target: unknown,
          time_horizon: unknown, constraints: [], risks: [], success_criteria: [],
        },
        requested_scope: { geography },
      });

    } catch (error) { notify(error.message || String(error), 'error'); renderApproval(); }
  };

  async function pollAnalysisJob(jobId, kind, domain, epoch) {
    for (;;) {
      if (epoch !== A.selectionEpoch) return;
      const job = await call('GET', `/api/jobs/${jobId}`);
      if (job.status === 'error') throw new Error(job.error || 'The analysis did not finish.');
      if (job.status === 'done') {
        if (epoch !== A.selectionEpoch) return;
        await selectBrand(domain, false);
        if (A.stage === 'think') await renderIntelligence();
        renderStrategy();
        notify(kind === 'strategy' ? 'We found the directions.' : 'The SWOT is ready for review.', 'success');
        return;
      }
      await new Promise(resolve => window.setTimeout(resolve, 800));
    }
  }

  async function decideStrategy(decision) {
    const reviewed = A.reviewedStrategy;
    const workflow = A.review;
    if (!reviewed || !workflow || workflow.status !== 'NEEDS_REVIEW' || workflow.subject_id !== reviewed.id || A.loadingBrand || A.busy) return;
    A.busy = true;
    try {
      await call('POST', `/api/review/${encodeURIComponent(workflow.workflow_id)}/resume`, {subject_id: workflow.subject_id, subject_hash: workflow.subject_hash, decision});
      if (A.selectionEpoch !== reviewed.epoch) return;
      await selectBrand(reviewed.domain, false);
      renderStrategy();
      notify(A.review && A.review.status === 'READY_FOR_CREATIVE' ? 'Strategy locked.' : A.review && A.review.status === 'REJECTED' ? 'Strategy version rejected.' : 'Review is blocked. Inspect the required actions.', A.review && ['READY_FOR_CREATIVE', 'REJECTED'].includes(A.review.status) ? 'success' : 'error');
    } catch (error) {
      if (error.status === 409 && A.selectionEpoch === reviewed.epoch) {
        await selectBrand(reviewed.domain, false); renderStrategy();
        notify('Strategy changed or needs repair. Review the current directions before deciding again.', 'error');
      } else if (A.selectionEpoch === reviewed.epoch) {
        await selectBrand(reviewed.domain, false);
        notify(errorMessage(error), 'error');
      }
    } finally { A.busy = false; renderApproval(); }
  }
  window.adzyLockStrategy = () => decideStrategy('approved');
  window.adzyRejectStrategy = () => decideStrategy('rejected');

  async function renderCreative() {
    if (!A.brand) return;
    const epoch = A.selectionEpoch;
    const historical = bestCreativeFor();
    A.contract = null; A.contractError = '';
    A.creative = historical;
    if (A.review) {
      try {
        const review = await call('GET', `/api/review/${encodeURIComponent(A.review.workflow_id)}`);
        if (epoch !== A.selectionEpoch) return;
        A.review = review;
        if (review.status === 'READY_FOR_CREATIVE') A.contract = review.creative_contract;
      } catch (error) {
        if (epoch !== A.selectionEpoch) return;
        A.review = null;
        A.contractError = errorMessage(error);
      }
    }
    renderBrief();
    let selected;
    try { selected=window.localStorage.getItem(revisionStorageKey()); } catch (_) {}
    const diagnostic=A.latestAttempt && A.latestAttempt.diagnosticPreview;
    if (diagnostic && !selected) renderPoster(diagnostic);
    else if (historical) renderPoster(historical); else resetPoster();
  }

  function lockIcon() {
    return '<svg class="adzy-brief-lock" viewBox="0 0 24 24" fill="none" aria-label="Locked by approved strategy"><rect x="4.5" y="10" width="15" height="10" rx="3" stroke="currentColor" stroke-width="1.8"/><path d="M8 10V7a4 4 0 1 1 8 0v3" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="15" r="1.2" fill="currentColor"/></svg>';
  }

  function friendlyTarget(value) {
    const full = text(value, 'Still unclear');
    const parts = full.split(';').map((part) => part.trim()).filter(Boolean);
    return { lead: parts[0] || full, qualifier: parts.slice(1).join('; ') };
  }

  function friendlyBoundary(value) {
    return text(value)
      .replace(/\bcl_[a-z0-9]+\b\s+is\s+/gi, '')
      .replace(/\bcl_[a-z0-9]+\b:?\s*/gi, '')
      .replace(/\ballowed_claim_ids\b/gi, 'the approved claim set')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function safeguardLabel(value) {
    const friendly = friendlyBoundary(value);
    const normalized = friendly.toLowerCase();
    if (/rank|#1|number one|best\b/.test(normalized)) return 'No unsupported rankings';
    if (/offer|discount|deal|price|promotion/.test(normalized)) return 'No unverified offers';
    if (/superior|better than|leading|most\b/.test(normalized)) return 'No unsupported superiority';
    return friendly;
  }

  function renderBrief() {
    const root = $('adzy-brief-rows');
    const contract = A.contract;
    $('adzy-brief-status').textContent = contract ? 'Locked by approved strategy' : 'Strategy approval and a valid Creative Direction required';
    if (!contract || !A.review || A.review.status !== 'READY_FOR_CREATIVE' || A.loadingBrand || A.brand.strategy_approval_state !== 'approved' || !A.brand.swot_approved) {
      if (!root.querySelector('.adzy-error')) root.innerHTML = '<div class="adzy-empty" style="margin-top:28px;">No approved Creative Direction was returned. Adzy will not invent WHO, SAY, PROVE or DO.</div>';
      $('adzy-make-btn').disabled = true;
      return;
    }
    $('adzy-make-btn').disabled = false;
    const proveState = truth.proofLine(contract);
    const boundaries = list(contract.prohibited_or_unverified_claims).map(friendlyBoundary).filter(Boolean);
    const safeguardLabels = Array.from(new Set(boundaries.map(safeguardLabel)));
    const target = friendlyTarget(contract.target);
    const rows = [
      ['WHO', target.lead, target.qualifier, ''],
      ['SAY', contract.primary_message, '', ''],
      ['PROVE', proveState.text, '', proveState.approved ? '' : 'is-empty-proof'],
      ['DO', contract.cta_direction, '', ''],
    ];
    const safeguards = safeguardLabels.length
      ? `<div class="adzy-safeguards">${safeguardLabels.slice(0, 4).map((item) => `<span>${esc(item)}</span>`).join('')}</div><details class="adzy-boundary-detail"><summary>All exact safeguards (${boundaries.length})</summary><ul>${boundaries.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></details>`
      : '<p class="adzy-brief-empty">No unsupported factual claims.</p>';
    root.innerHTML = rows.map(([label, value, qualifier, state]) => `<div class="adzy-brief-row ${state}"><dt>${label}</dt><dd dir="auto">${esc(sentence(value))}</dd>${qualifier ? `<div class="adzy-brief-qualifier">Locked qualification · ${esc(qualifier)}</div>` : ''}${lockIcon()}</div>`).join('')
      + `<div class="adzy-brief-row adzy-dont-row"><dt>DON'T SAY</dt>${safeguards}${lockIcon()}</div>`;
  }

  function resetPoster() {
    if (window.adzyCopyPosterChanged) window.adzyCopyPosterChanged(null);
    const stage = $('adzy-poster-stage');
    stage.classList.remove('has-result', 'is-assembling');
    $('adzy-result-actions').hidden = true;
    $('adzy-why').hidden = true;
    $('adzy-copy-evidence').innerHTML = '';
    $('adzy-candidate-status').hidden = true;
  }

  function qaState(item, kind) {
    if (kind === 'strategy') {
      const verification = item.strategy_verification || {};
      if (verification.overall_pass === true) return ['Strategy aligned', 'pass'];
      if (verification.overall_pass === false) return ['Strategy not aligned', 'fail'];
      return ['Strategy QA unavailable', 'unknown'];
    }
    if (kind === 'claims') {
      const assessment = item.factual_assessment;
      if (assessment) return [assessment.status === 'SUPPORTED' && item.factual_eligible === true ? 'Authored facts match source quotations · semantics unqualified' : 'Copy unresolved · draft only', assessment.status === 'SUPPORTED' && item.factual_eligible === true ? 'pass' : 'unknown'];
      if (list(item.claim_ids_used).length || list((item.strategy_verification || {}).claim_ids_used).length) return ['Approved claim references · factual verification unavailable', 'unknown'];
      return ['Claim check unavailable', 'unknown'];
    }
    const status = text(item.qa_status).toLowerCase();
    if (status === 'pass') return [text((item.visual_qa || {}).reason).startsWith('SYNTHETIC_TEST:') ? 'Synthetic QA passed · test evidence only' : 'Visual QA passed', 'pass'];
    if (status === 'fail' || item.qa_failed) return ['Visual QA failed', 'fail'];
    return ['Visual QA not checked', 'unknown'];
  }

  function renderPoster(item) {
    if (!item || !creativeImage(item)) return resetPoster();
    A.creative = item;
    const stage = $('adzy-poster-stage');
    stage.classList.remove('is-assembling');
    stage.classList.add('has-result');
    $('adzy-result-image').src = creativeImage(item);
    const contract = item.creative_contract || A.contract || {};
    $('adzy-result-left').innerHTML = `<p class="adzy-mono">${esc(brandName())} / 001</p><h4>The direction</h4><p>${esc(sentence(contract.primary_message || contract.consumer_value || contract.positioning_statement))}</p><h4>Audience</h4><p>${esc(friendlyTarget(contract.target).lead)}</p><h4>CTA</h4><p>${esc(sentence(contract.cta_direction))}</p>`;
    $('adzy-qa-list').innerHTML = ['strategy', 'claims', 'visual'].map((kind) => {
      const [label, status] = qaState(item, kind);
      return `<div class="adzy-qa-item is-${status}"><i class="adzy-dot"></i>${esc(label)}</div>`;
    }).join('');
    $('adzy-result-actions').hidden = false;
    const diagnostic = item.diagnostic === true;
    stage.classList.toggle('is-diagnostic', diagnostic);
    $('adzy-result-left').hidden = diagnostic;
    if (diagnostic) {
      const [label, status] = qaState(item, 'visual');
      $('adzy-qa-list').innerHTML = `<div class="adzy-qa-item is-${status}"><i class="adzy-dot"></i>${esc(label)}</div>`;
    }
    $('adzy-candidate-status').hidden = !diagnostic;
    if (diagnostic) {
      const rejected = item.qa_status === 'fail' || (item.visual_qa || {}).overall_pass === false;
      $('adzy-candidate-status').innerHTML = `<h3>${rejected ? 'Candidate rejected' : 'Candidate not verified'}</h3><p>${esc(text((item.visual_qa || {}).reason, 'Required checks did not complete.'))}</p><button type="button" class="adzy-btn ink" onclick="adzyInspectCandidateChecks()">Review saved checks</button>`;
      $('adzy-generation-error').hidden = true;
    }
    $('adzy-retry-poster').hidden = diagnostic;
    $('adzy-why-action').hidden = diagnostic;
    $('adzy-trace-action').hidden = diagnostic;
    $('adzy-last-accepted').hidden = !A.lastAccepted || A.lastAccepted.generation_run_id === item.generation_run_id;
    $('adzy-retry-poster').disabled = !A.contract || A.loadingBrand || !A.brand.swot_approved || A.brand.strategy_approval_state !== 'approved' || A.busy;
    const disclosure = [];
    if (item.strategy_version_id && item.strategy_version_id !== A.brand.strategy_version_id) disclosure.push('Earlier strategy version. Review the current strategy before generating again.');
    if (item.accepted !== true) disclosure.push('Diagnostic creative only. Not accepted or deliverable.');
    if (text((item.visual_qa || {}).reason).startsWith('SYNTHETIC_TEST:')) disclosure.push('Synthetic provider and QA test evidence. Not live model validation.');
    if (item.diagnostic) disclosure.push('Saved rejected or unverified candidate · inspection only · not an accepted download. ' + text((item.visual_qa || {}).reason));
    if (item.trust_violation) disclosure.push(text(item.trust_note_ar, 'Trust limitation returned by QA.'));
    if (item.qa_status === 'fail' || item.qa_failed) disclosure.push(text(item.qa_note, 'Visual QA did not pass.'));
    if (!item.qa_status) disclosure.push('QA status unavailable for this older creative.');
    if (disclosure.length) $('adzy-result-left').insertAdjacentHTML('beforeend', `<div class="adzy-disclosure">${esc(disclosure.join(' · '))}</div>`);
    const assessment = item.factual_assessment;
    $('adzy-copy-evidence').innerHTML = '';
    if (assessment) {
      const claims = list(assessment.propositions).map(p => {
        const j = list(assessment.judgments).find(j => j.proposition_id === p.proposition_id) || {};
        const sources = list(j.evidence).map(e => `<blockquote>${esc(e.quote)}</blockquote><a href="${esc(/^https?:\/\//i.test(e.page_url || '') ? e.page_url : '#')}" target="_blank" rel="noopener noreferrer">Source page</a>`).join('');
        const considered = (p.kind === 'NONFACTUAL_INVITATION' ? [] : list(j.considered_evidence)).map(e => `<blockquote>Considered, not support: ${esc(e.quote)}</blockquote><a href="${esc(/^https?:\/\//i.test(e.page_url || '') ? e.page_url : '#')}" target="_blank" rel="noopener noreferrer">Source page</a>`).join('');
        const actions = {REQUEST_EVIDENCE_OR_REVIEW:'Request evidence or review', REMOVE:'Remove this statement', ABSTAIN:'Leave this wording out'};
        const verdicts = {SUPPORTED:'Supported by source quotation', CONTRADICTED:'Contradicted by evidence', INSUFFICIENT_EVIDENCE:'Not enough evidence', UNAVAILABLE:'Check unavailable'};
        return `<li><b>${esc(p.text)}</b> · ${esc(p.location)}<p>${esc(p.kind === 'NONFACTUAL_INVITATION' ? 'Invitation · excluded from factual checks' : verdicts[j.verdict] || 'Check unavailable')}: ${esc(j.reason)}</p>${sources}${considered ? `<details><summary>Evidence considered, without established support</summary>${considered}</details>` : ''}<p>${esc(p.kind === 'NONFACTUAL_INVITATION' ? '' : list(j.actions).map(a => actions[a] || 'Review wording').join(' · '))}</p></li>`;
      }).join('');
      $('adzy-copy-evidence').insertAdjacentHTML('beforeend', `<details><summary>Copy evidence · ${esc(assessment.status)}</summary><p>Authored copy only. Text inside image pixels is not assessed. Semantic method unqualified. Source assertions do not establish independent truth.</p><ul>${claims}</ul><p>Unresolved: remove the statement, use the exact supported wording, request evidence or review, or abstain. Any edit requires a new assessment.</p></details>`);
    }
    if (item.diagnostic) $('adzy-copy-evidence').innerHTML = '<details><summary>Saved candidate checks · inspection only</summary><ul>'
      + list(item.candidate_history).map(c => `<li>${esc(c.identity.candidate_id)} · ${esc(c.checksum.slice(0,12))} · ${esc(c.visual_qa.checked ? (c.visual_qa.overall_pass === true ? 'Visual QA passed; final checks unresolved' : 'QA rejected / not accepted') : 'QA not completed')}<p>${esc(c.visual_qa.reason)}</p></li>`).join('') + '</ul></details>';
    refreshDelivery(item);
    renderLineage();
    if (item.copy_revision) {
      $('adzy-generation-error').hidden = false;
      $('adzy-generation-error').textContent = 'Saved copy revision · ' + (item.accepted ? 'inspect current delivery checks.' : 'draft; this render needs its own visual review.');
    }
    if (window.adzyCopyPosterChanged) window.adzyCopyPosterChanged(item);
  }

  window.adzySelectCopyRevision = function(item) {
    if (!A.history.some(r => r.generation_run_id === item.generation_run_id)) A.history.unshift(item);
    renderPoster(item); rememberRevision(item); renderWork();
  };

  function revisionStorageKey() { return operationStorageKey() + ':selected-revision'; }

  window.adzyInspectCandidateChecks = function () {
    const section = $('adzy-copy-evidence'), detail = section.querySelector('details');
    if (detail) detail.open = true;
    section.scrollIntoView({block:'center', behavior:'auto'});
  };

  function rememberRevision(item) {
    if (item && item.generation_run_id) {
      try { window.localStorage.setItem(revisionStorageKey(), item.generation_run_id); } catch (_) { /* server remains authoritative */ }
    }
  }

  async function refreshDelivery(item) {
    const final = $('adzy-download-final'), draft = $('adzy-download-draft');
    final.disabled = true; draft.disabled = true;
    final.hidden = true; draft.hidden = true;
    $('adzy-delivery-note').textContent = 'Checking this saved revision for delivery…';
    if (!item.canonical || !item.generation_run_id) {
      $('adzy-delivery-note').textContent = item.diagnostic
        ? 'Inspection only: this candidate has no accepted Asset. Required final delivery checks have not completed, so no download is available.'
        : 'Preview only: this image has no canonical run and Asset binding. Exact revision delivery checks cannot be performed.';
      return;
    }
    try {
      const status = await call('GET', `/api/poster/runs/${encodeURIComponent(item.generation_run_id)}/delivery`);
      if (A.creative !== item) return;
      item.delivery = status;
      final.disabled = !status.final_eligible; draft.disabled = !status.draft_export_available;
      final.hidden = !status.final_eligible;
      draft.hidden = status.final_eligible || !status.draft_export_available;
      $('adzy-delivery-note').textContent = status.final_eligible
        ? 'This exact revision passes the current delivery checks. Authored copy matches source quotations; image text and general meaning are not certified.'
        : 'Draft inspection only. Current approval, revision or required checks need review. Exporting a draft does not verify it.';
    } catch (_) {
      if (A.creative === item) $('adzy-delivery-note').textContent = 'Delivery unavailable. Check the saved image and revision, then refresh status.';
    }
  }

  window.adzyDownloadPoster = async function (mode) {
    const item = A.creative;
    if (!item || !item.generation_run_id) return;
    try {
      // Obtain fresh authority; download rechecks the same revision again on the server.
      const status = await call('GET', `/api/poster/runs/${encodeURIComponent(item.generation_run_id)}/delivery`);
      if (A.creative !== item) return;
      if (mode === 'final' && !status.final_eligible) throw new Error('This revision is a draft or needs current approval. Inspect the checks or export it as a draft.');
      const params = new URLSearchParams({mode, attempt_id: status.generation_attempt_id, checksum: status.checksum});
      const response = await window.fetch(`/api/poster/runs/${encodeURIComponent(item.generation_run_id)}/download?${params}`, {headers: window.authHeaders()});
      if (!response.ok) throw new Error('The revision is no longer eligible or its bytes changed. Refresh its check status.');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${mode}-${status.asset_id}-${status.checksum.slice(0,12)}.${status.mime === 'image/png' ? 'png' : status.mime === 'image/jpeg' ? 'jpg' : 'webp'}`;
      anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      rememberRevision(item);
    } catch (error) { notify(errorMessage(error), 'error'); }
  };

  window.adzyShowLastAccepted = function () {
    if (A.lastAccepted) { renderPoster(A.lastAccepted); rememberRevision(A.lastAccepted); }
  };

  function operationStorageKey() {
    const owner = (window.adzyLegacyState.currentUser || {}).id;
    if (!owner || !A.brand) throw new Error('Reload your authenticated account before generating.');
    return `adzy.poster.operation:${owner}:${A.brand.domain_key}`;
  }

  function savedOperation() {
    const raw = window.localStorage.getItem(operationStorageKey());
    return raw ? JSON.parse(raw) : null;
  }

  function projectAttempt(operation) {
    A.latestAttempt = operation;
    if (!operation) return;
    if (['accepted', 'draft'].includes(operation.state) && operation.result) {
      renderPoster(operation.result);
      if (!A.history.some(item => item.generation_run_id === operation.generation_run_id)) A.history.unshift(operation.result);
      renderWork(); renderParade();
    }
    if (!['accepted','draft'].includes(operation.state) && list(operation.candidates).length) {
      const candidate=operation.candidates[operation.candidates.length-1];
      const epoch=A.selectionEpoch;
      call('GET', `/api/poster/operations/${encodeURIComponent(operation.operation_key)}/candidates/${encodeURIComponent(candidate.identity.candidate_id)}/preview?as_json=true`)
        .then(item => { operation.diagnosticPreview=item; if(A.latestAttempt===operation && A.selectionEpoch===epoch) renderPoster(item); })
        .catch(() => { /* Metadata remains visible if bytes are unavailable. */ });
    }
    const messages = {
      admitted: 'Poster admitted. Check status to recover this attempt.',
      running: 'Poster in progress. Check status; no additional request will be sent.',
      failed: 'Latest attempt failed. No accepted result was created by this attempt.',
      unknown: 'Latest attempt has an unknown outcome. Reconciliation with the provider is required. No automatic resend.',
      accepted: 'Latest attempt accepted and saved in the gallery.',
      draft: 'Saved as a draft; not accepted or deliverable. Inspect the copy and visual checks to resolve required checks.',
    };
    let note = messages[operation.state] || 'Attempt status unavailable. Check status.';
    if (operation.reason_code === 'POSTER_CALL_PRICING_UNKNOWN' || operation.reason_code === 'POSTER_BUDGET_CONFIGURATION_REQUIRED') note += ' Pricing configuration needs operator review.';
    if (operation.reason_code === 'POSTER_STAGE_BUDGET_EXHAUSTED') note += ' The reserved estimate envelope was exhausted.';
    if (operation.state === 'unknown') {
      note += ` Attempt reference: ${operation.operation_key}.`;
      const references = list(operation.calls).map(call => call.provider_reference).filter(Boolean);
      if (references.length) note += ` Provider references: ${references.join(', ')}.`;
    }
    $('adzy-generation-error').hidden = false;
    if (list(operation.candidates).length) note += ' Saved candidate: ' + text(operation.candidates[operation.candidates.length-1].visual_qa.reason);
    $('adzy-generation-error').textContent = note + (A.creative && !list(operation.candidates).length && !['accepted', 'draft'].includes(operation.state) ? ' The displayed image is the previous saved result.' : '');
    if (!['accepted','draft'].includes(operation.state)) $('adzy-generation-error').insertAdjacentHTML('beforeend', '<button type="button" class="adzy-btn flat" onclick="adzyCheckPosterStatus()">Check attempt status</button>');
  }

  async function restorePosterAttempt(epoch) {
    const payload = await call('GET', `/api/poster/operations/latest?domain_key=${encodeURIComponent(A.brand.domain_key)}`);
    if (epoch !== A.selectionEpoch) return;
    let selected;
    try { selected = window.localStorage.getItem(revisionStorageKey()); } catch (_) {}
    A.lastAccepted = payload.last_accepted;
    $('adzy-last-accepted').hidden = !payload.last_accepted;
    if (payload.last_accepted) renderPoster(payload.last_accepted);
    projectAttempt(payload.operation);
    const pending = savedOperation();
    if (pending && (!payload.operation || pending.operation_key !== payload.operation.operation_key)) {
      try {
        const operation = await call('GET', `/api/poster/operations/${encodeURIComponent(pending.operation_key)}`);
        if (epoch === A.selectionEpoch) projectAttempt(operation);
      } catch (_) {
        if (epoch === A.selectionEpoch) {
          $('adzy-generation-error').hidden = false;
          $('adzy-generation-error').textContent = 'A saved request needs recovery. Make the ad again to retry the same operation key.';
        }
      }
    }
    const selectedItem = A.history.find(item => item.generation_run_id === selected);
    if (selectedItem) { renderPoster(selectedItem); rememberRevision(selectedItem); }
  }

  window.adzyCheckPosterStatus = async function () {
    const epoch = A.selectionEpoch;
    try { await restorePosterAttempt(epoch); }
    catch (error) { notify(errorMessage(error), 'error'); }
  };

  window.adzyMakeAd = async function (newVariation = false) {
    if (!A.brand || !A.contract || !A.review || A.review.status !== 'READY_FOR_CREATIVE' || A.loadingBrand || !A.brand.swot_approved || A.busy) { notify('An approved Creative Direction is required.', 'error'); return; }
    const epoch = A.selectionEpoch;
    A.busy = true;
    const stage = $('adzy-poster-stage');
    const button = $('adzy-make-btn');
    button.disabled = true; $('adzy-retry-poster').disabled = true;
    try {
      if (A.latestAttempt && ['running', 'admitted', 'unknown'].includes(A.latestAttempt.state)) {
        await window.adzyCheckPosterStatus(); return;
      }
      let request = savedOperation();
      if (newVariation && request && (!A.latestAttempt || A.latestAttempt.operation_key !== request.operation_key)) {
        const recovered = await call('GET', `/api/poster/operations/${encodeURIComponent(request.operation_key)}`);
        if (epoch !== A.selectionEpoch) return;
        projectAttempt(recovered);
        if (!['accepted', 'draft', 'failed'].includes(recovered.state)) return;
      }
      if (newVariation || !request) {
        request = {
          operation_key: window.crypto.randomUUID(), domain_key: A.brand.domain_key,
          content_type: 'brand_poster', style: 'strategy_grounded', headline: '', subtext: '',
          cta: '', user_context: '', mode: 'text_to_image', language: A.contract.language || 'auto',
          trend: false, content_only: false, product_url: '', product_name: '', product_description: '',
          strategy_version_id: A.contract.strategy_version_id,
        };
        // Persist BEFORE dispatch. Failure to persist refuses generation.
        window.localStorage.setItem(operationStorageKey(), JSON.stringify(request));
      }
      stage.classList.add('is-assembling');
      const operation = await call('POST', '/api/generate', request);
      if (epoch !== A.selectionEpoch) return;
      projectAttempt(operation);
      rememberRevision(operation.result);
    } catch (error) {
      if (epoch !== A.selectionEpoch) return;
      $('adzy-generation-error').hidden = false;
      $('adzy-generation-error').textContent = errorMessage(error) + ' Response unavailable. Check attempt status or retry the saved operation; its key is retained.';
      $('adzy-generation-error').insertAdjacentHTML('beforeend', '<button type="button" class="adzy-btn flat" onclick="adzyCheckPosterStatus()">Check attempt status</button>');
      notify('Poster status needs recovery. The response does not prove failure.', 'error');
    } finally {
      stage.classList.remove('is-assembling'); A.busy = false;
      if (epoch === A.selectionEpoch) {
        button.disabled = !A.contract || A.loadingBrand;
        $('adzy-retry-poster').disabled = !A.contract || A.loadingBrand;
      }
    }
  };

  function resolveLineage() {
    const item = A.creative || {};
    const contract = item.creative_contract || A.contract || {};
    const candidate = (A.brand && A.brand.strategy_candidate) || {};
    const selected = strategyData().selected || {};
    const claims = claimMap();
    const evidence = evidenceMap();
    const claimIds = list(item.claim_ids_used).length ? list(item.claim_ids_used) : list(contract.consumer_value_claim_ids || contract.supporting_claim_ids);
    const claim = claims.get(text(claimIds[0])) || list(contract.allowed_claims)[0] || null;
    const evidenceId = claim && list(claim.supporting_evidence_ids)[0];
    const evidenceRow = evidence.get(text(evidenceId)) || null;
    const targetLead = friendlyTarget(selected.target || contract.target).lead;
    const directionLead = text(selected.consumer_value || selected.differentiated_value || contract.primary_message || selected.positioning_statement);
    const evidenceLimit = evidenceRow ? list(evidenceRow.limitations).map(text).filter(Boolean).join(' · ') : '';
    const claimLimit = claim ? list(claim.limitations).map(text).filter(Boolean).join(' · ') : '';
    return {
      evidence: text(evidenceRow && (evidenceRow.excerpt || evidenceRow.raw_value), 'No linked source observation is exposed for this gallery item.'),
      evidenceType: evidenceRow ? `${text(evidenceRow.source_class, 'Evidence').replaceAll('_', ' ')} · ${text(evidenceRow.availability, 'availability unknown')}` : 'Evidence relationship unavailable',
      evidenceLimit,
      claim: text(claim && claim.statement, 'No separate inferred insight persisted at this stage.'),
      claimType: claim ? `${text(claim.epistemic_type, 'Claim').replaceAll('_', ' ')} · ${text(claim.scope && claim.scope.role, 'role unspecified').replaceAll('_', ' ')}` : 'Claim artifact unavailable',
      claimLimit,
      strategy: directionLead ? `For ${targetLead}: ${directionLead}` : 'No selected positioning returned.',
      message: text(contract.primary_message || (item.why_this_poster || {}).message, 'No primary message returned.'),
      poster: `${brandName()} campaign poster`,
      full: !!(evidenceRow && claim && text(selected.positioning_statement || contract.positioning_statement) && text(contract.primary_message)),
    };
  }

  function renderLineage() {
    const root = $('adzy-lineage');
    const data = resolveLineage();
    const steps = [
      ['01', 'Evidence / observation', 'We saw', data.evidence, data.evidenceType, data.evidenceLimit, 'var(--adzy-mango)'],
      ['02', 'Claim / inference', 'We learned', data.claim, data.claimType, data.claimLimit, 'var(--adzy-sky)'],
      ['03', 'Approved positioning', 'We chose', data.strategy, 'StrategyVersion', '', 'var(--adzy-grape)'],
      ['04', 'Approved message', 'We said', data.message, 'CreativeContract', '', 'var(--adzy-watermelon)'],
      ['05', 'Saved asset', 'We made', data.poster, 'Poster artifact', '', 'var(--adzy-mint)'],
    ];
    root.innerHTML = steps.map(([num, artifact, title, value, type, limitation, color]) => `<article class="adzy-lineage-step" style="--lineage-color:${color}"><div class="adzy-lineage-top"><span class="num">${num}</span><span>${esc(artifact)}</span></div><h3>${title}</h3><p>${esc(value)}</p><small>${esc(type)}</small>${limitation ? `<em>${esc(limitation)}</em>` : ''}</article>`).join('');
    $('adzy-trace-gap').textContent = data.full ? 'Trace resolved through the approved strategy, claim and evidence chain.' : 'Deepest honest trace shown. This gallery item does not expose every supporting relationship, so Adzy stops here rather than inventing one.';
  }

  window.adzyOpenWhy = function () {
    renderLineage();
    $('adzy-why').hidden = false;
    $('adzy-why').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  };

  window.adzyOpenTrace = function () {
    const data = resolveLineage();
    const rows = [
      ['Approved message · CreativeContract', data.message, 'var(--adzy-watermelon)'],
      ['Comes from', 'Approved consumer value in the Creative Direction', 'var(--adzy-mango)'],
      ['Approved positioning · StrategyVersion', data.strategy, 'var(--adzy-grape)'],
      [`Claim · ${data.claimType}`, `${data.claim}${data.claimLimit ? ` · Limitation: ${data.claimLimit}` : ''}`, 'var(--adzy-sky)'],
      [`Evidence · ${data.evidenceType}`, `${data.evidence}${data.evidenceLimit ? ` · Limitation: ${data.evidenceLimit}` : ''}`, 'var(--adzy-mint)'],
    ];
    $('adzy-trace-rows').innerHTML = rows.map(([label, value, color]) => `<div class="adzy-trace-row" style="--trace-color:${color}"><small>${label}</small><strong>${esc(value)}</strong></div>`).join('');
    $('adzy-trace-overlay').classList.add('is-open');
    $('adzy-trace-overlay').querySelector('.adzy-drawer-close').focus();
  };

  window.adzyCloseTrace = function () { $('adzy-trace-overlay').classList.remove('is-open'); };

  function renderWork() {
    const root = $('adzy-work-wall');
    if (!root) return;
    const items = curatedCreatives(9);
    if (!items.length) {
      root.innerHTML = '<div class="adzy-empty">No work has been returned for this account yet.</div>';
      return;
    }
    const notes = [
      '<div class="adzy-work-note" style="--note-color:var(--adzy-blue);--note-ink:white"><span class="adzy-mono">Archive note</span><strong>Strategy<br>before pixels.</strong></div>',
      '<div class="adzy-work-note" style="--note-color:var(--adzy-mango);--note-ink:var(--adzy-ink)"><span class="adzy-mono">Adzy principle</span><strong>Make the<br>proof visible.</strong></div>',
    ];
    const cards = items.map((item, index) => {
      const status = item.canonical && item.strategy_grounded ? 'Strategy grounded' : 'Earlier creative · strategy link unavailable';
      const sourceIndex = creatives().findIndex((candidate) => creativeImage(candidate) === creativeImage(item));
      return `<figure class="adzy-work-item ${index === 0 ? 'is-hero' : ''}" tabindex="0" onclick="adzyShowWork(${sourceIndex})"><img src="${creativeImage(item)}" loading="lazy" alt="${esc(item.brand_name || 'Adzy campaign creative')}"><figcaption class="adzy-work-caption"><strong>${esc(item.brand_name || item.domain_key || 'Adzy')}</strong><span>${esc(status)} · ${esc(item.content_type || 'poster')}</span></figcaption></figure>`;
    });
    root.innerHTML = [cards[0], notes[0], ...cards.slice(1, 3), notes[1], ...cards.slice(3)].join('');
  }

  window.adzyShowWork = async function (index) {
    const item = creatives()[index];
    if (!item) return;
    if (item.domain_key && (!A.brand || A.brand.domain_key !== item.domain_key)) await selectBrand(item.domain_key, false);
    A.creative = item;
    A.contract = item.creative_contract || A.contract;
    await window.adzyNavigate('create');
    renderPoster(item);
    rememberRevision(item);
  };

  async function bootstrapData() {
    const token = window.adzyLegacyState && window.adzyLegacyState.authToken;
    if (!token) return;
    if (bootstrapFlight && bootstrapFlight.token === token) return bootstrapFlight.promise;
    const flight = {token};
    bootstrapFlight = flight;
    flight.promise = (async () => {
      try {
        await Promise.all([refreshBrands(), refreshHistory()]);
        if (window.adzyLegacyState.authToken !== token) return;
        updateBrandSurfaces();
        const stage = A.initialLocation.get('stage');
        if (!initialStageRestored && A.brand) {
          initialStageRestored = true;
          if (['think', 'strategy', 'create', 'work'].includes(stage)) await window.adzyNavigate(stage);
        }
      } catch (_) { /* auth gate remains the authority for an unavailable session */ }
    })();
    try { await flight.promise; }
    finally { if (bootstrapFlight === flight) bootstrapFlight = null; }
  }

  function bridgeLegacyAuth() {
    const legacyLoadBrands = window.loadBrands;
    if (typeof legacyLoadBrands === 'function') {
      window.loadBrands = async function () {
        const result = await legacyLoadBrands.apply(this, arguments);
        await bootstrapData();
        return result;
      };
    }
    const legacyLogout = window.logout;
    if (typeof legacyLogout === 'function') {
      window.logout = function () {
        if (window.adzyCopyLogout) window.adzyCopyLogout();
        ++A.selectionEpoch;
        initialStageRestored = false;
        A.reviewedSwot = null; A.reviewedStrategy = null; A.loadingBrand = false; A.busy = false; A.contractError = '';
        A.review = null; A.reviewError = ''; A.brand = null; A.brands = []; A.history = []; A.contract = null; A.creative = null; A.latestAttempt = null;
        return legacyLogout.apply(this, arguments);
      };
    }
  }

  function init() {
    document.body.classList.add('adzy-ready');
    $('adzy-app').hidden = false;
    setupIntro();
    setupTopbar();
    bridgeLegacyAuth();
    window.setTimeout(() => { if (!initialStageRestored) bootstrapData(); }, 350);
  }

  init();
}());
