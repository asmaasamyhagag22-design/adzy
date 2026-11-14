(function () {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[ch]);
  const messages = {
    READY: 'أُغلقت فجوة الدليل',
    BUDGET_EXHAUSTED: 'انتهت ميزانية البحث قبل اكتمال الدليل',
    NO_SUPPORTED_OFFER: 'انتهى البحث من دون عرض صالح',
    BRAND_ENTRY: 'بدء البحث من موقع البراند',
    GAP_MATCH: 'الرابط يرجّح وجود السعر أو الشروط الناقصة',
    OWNER_SOURCE: 'صفحة اقترحها صاحب البراند',
    RECHECK_BOUND_EVIDENCE: 'إعادة قراءة الصفحة التي يعتمد عليها الإعلان',
    SUPPORTED_OFFER: 'وُجد السعر والفرع والشروط والصلاحية في المصدر',
    OFFER_NOT_FOUND: 'الصفحة لا تحتوي عرضًا منظمًا',
    SOURCE_UNAVAILABLE: 'تعذر الوصول إلى المصدر',
    SOURCE_PARTIAL_RESPONSE: 'المصدر رجّع جزءًا من الصفحة. مطلوب التقاط الصفحة كاملة.',
    UNSUPPORTED_OFFER_PAGE_CONTENT: 'الصفحة تحتاج مراجعة بسبب محتوى خارج الصيغة المدعومة',
    UNPARSED_OFFER_CONTEXT: 'ظهر سياق إضافي يحتاج مراجعة قبل صياغة الإعلان',
    OFFER_EXPIRED: 'انتهت صلاحية العرض',
    EVIDENCE_GAP: 'الأدلة غير كافية لاعتماد الإعلان.',
    PROFILE_CHANGED: 'تغيّر بروفايل البراند. ابدأ بحثًا على النسخة الجديدة.',
    QUOTE_OBSERVED: 'وُجد الاقتباس في المصدر',
    QUOTE_NOT_FOUND: 'الاقتباس غير موجود في متن الصفحة',
    QUOTE_CONTEXT_AMBIGUOUS: 'ظهر الاقتباس في سياقات مختلفة',
    BRAND_SOURCE_ORIGIN_MISMATCH: 'اختَر رابطًا من نفس أصل موقع البراند.',
    PEER_SOURCE_MUST_BE_DISTINCT: 'اختَر موقع منافس من نطاق مستقل.',
    EXPORT_BUSY: 'فيه طلب بوستر شغّال. جرّب بعد ما يخلص.',
    POSTER_CLIPPING: 'النص أطول من مساحة البوستر. عدّل اتجاه الحملة وراجع المعاينة.',
    POSTER_RENDER_UNAVAILABLE: 'تعذّر تشغيل محرّك رسم البوستر.',
    POSTER_PREVIEWS_REQUIRED: 'اعرض المقاسين قبل الاعتماد.',
    POSTER_PREVIEW_CHANGED: 'تغيّرت معاينة البوستر. اعرض المقاسين من جديد.',
    POSTER_ARTIFACT_CORRUPT: 'تلفت معاينة البوستر المحفوظة.',
    POSTER_ARTIFACT_UNAVAILABLE: 'تعذّر قراءة معاينات البوستر.',
    APPROVED_ARTIFACT_CORRUPT: 'تعذّر التحقق من الحزمة المعتمدة. لازم تستعيد النسخة المحفوظة.',
    CAMPAIGN_ARCHIVE_TOO_LARGE: 'حجم حزمة البوسترات أكبر من الحد المتاح.',
    CAMPAIGN_REVIEW_CHANGED: 'تغيّر قرار مراجعة الحملة. راجع حالتها الحالية.',
    CURRENT_PACKAGE_APPROVAL_REQUIRED: 'التنزيل يتطلب اعتماد الحزمة الحالية.',
    SOURCE_RECHECK_REQUIRED: 'أعد فحص المصادر قبل اعتماد الحزمة.',
    COPY_REQUIRES_EVIDENCE_REVIEW: 'الصياغة تحتاج مراجعة دليل. استخدم النص المحافظ أو صياغة مقبولة من نفس المصدر.',
    BRIEF_REVIEW_REQUIRED: 'راجع الـCreative brief عشان تجهّز الحزمة.',
    PLANNED_BRIEF_REQUIRED: 'ابدأ البحث عشان يظهر الـbrief المقترح.',
    CAMPAIGN_RESEARCH_BUSY: 'فيه بحث شغّال. جرّب بعد ما يخلص.',
    INVALID_SOURCE_URL: 'أدخل رابطًا عامًا صالحًا للبراند.',
    FEATURE_EVIDENCE: 'لسه محتاجين دليل للميزة المرتبطة بالهدف',
    QUALIFIED_ACTION: 'شروط الميزة تحتاج مراجعة قبل التوجيه للتنزيل',
    ACTION_DESTINATION: 'محتاجين صفحة الإجراء الرسمية',
    COMMERCIAL_TERMS: 'محتاجين شروط العرض أو الإجراء المنشورة',
    EDITORIAL_SOURCE: 'مصدر مرتبط بالاتجاه التحريري',
    EDITORIAL_SOURCE_GAP: 'تغيّر دليل مطلوب أو تعذّر فتحه',
    SOURCE_FIELD_CHANGED: 'النص المنشور تغيّر',
    SOURCE_SUBJECT_CHANGED: 'عنوان الصنف المرتبط بالاقتباس تغيّر',
    RETRY_SOURCE_TIMEOUT: 'محاولة ثانية بعد انتهاء المهلة',
    SOURCE_DEADLINE: 'انتهت ميزانية وقت البحث عن المصدر',
    SOURCE_TIMEOUT: 'انتهت مهلة قراءة المصدر',
    SOURCE_FIELD_MISSING: 'الحقل المطلوب اختفى من الصفحة',
    FOLLOW_SOURCE_REDIRECT: 'متابعة التحويل داخل نفس الموقع',
    SOURCE_REDIRECT_OUT_OF_SCOPE: 'التحويل يخرج عن الموقع المحدد',
    DIRECTION_NOT_FOUND: 'الاتجاه مش موجود في النسخة الحالية.',
    SOURCE_READ: 'تمت قراءة المصدر',
    SOURCE_CONFLICT: 'المصادر المنشورة تشير إلى تعارض يحتاج مراجعة',
    SOURCE_REDIRECT: 'الرابط يحوّل إلى صفحة أخرى',
    FILL_FEATURE_GAP: 'فتح صفحة يُحتمل إنها تشرح الميزة',
    MORE_SPECIFIC_FEATURE: 'بحث عن صفحة أكثر تحديدًا للميزة',
    FIND_COMMERCIAL_TERMS: 'بحث عن الشروط المنشورة',
    DECISION_EVIDENCE_FOUND: 'وُجد دليل مبدئي للقرار',
    NO_MORE_RELEVANT_SOURCES: 'خلصت الروابط ذات الصلة',
    CAMPAIGN_CHANGED: 'تغيّرت نسخة الحملة. راجع النسخة المعروضة.',
  };
  const label = value => messages[value] || value;
  let brand, state, timer, lastPlan, requestNumber = 0, busy = false;
  let previews = {};

  function path(suffix = '') {
    return '/api/v2/campaigns/' + encodeURIComponent(brand.domain_key) + suffix;
  }

  function link(value, text) {
    try {
      const url = new URL(value);
      if (['http:', 'https:'].includes(url.protocol)) {
        return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
      }
    } catch (_) {}
    return esc(text);
  }

  function showStatus(text) {
    byId('v2-status').textContent = text;
  }

  function card(proposal) {
    const {copy, page_url, sha256, brand_name} = proposal.creative;
    return `<article class="v2-creative" id="v2-creative" aria-label="الإبداع العربي">
      ${brand_name ? `<div class="v2-creative-header" dir="auto">${esc(brand_name)}</div>` : ''}
      <h2>${esc(copy.headline)}</h2>
      <p class="v2-price" dir="ltr">${esc(copy.price)}</p>
      <p>${esc(copy.branch)}</p>
      <p>${esc(copy.terms)}</p>
      <p class="v2-validity">ساري حتى <bdi>${esc(copy.validity)}</bdi></p>
      <p class="v2-creative-cta">${link(page_url, copy.cta)}</p>
      <details><summary>بصمة محتوى النسخة</summary><code dir="ltr">${esc(sha256)}</code></details>
    </article>`;
  }

  function packagePanel(proposal, approved, reason, decision) {
    const {brief, evidence, planner, package: creative} = proposal;
    const disclosure = creative?.art?.disclosure || 'الرسم توضيحي.';
    const fieldNames = {headline:'العنوان', message:'الرسالة', cta:'الدعوة', caption:'نص المنشور', art:'المشهد البصري'};
    const sources = evidence.map((item, index) => `<li id="v2-source-${index}">
      <strong>${planner?.edition ? esc(item.scope) : index === 0 ? 'البراند' : planner ? 'صفحة الإجراء' : 'المنافس المقترح'} · ${esc(label(item.outcome))}</strong>
      <p>${link(item.page_url, item.page_url)}</p>
      ${item.quote ? `${planner?.edition ? "" : `<p>${esc(item.scope)}</p>`}<blockquote dir="auto">${esc(item.quote)}</blockquote>` : ''}
      ${item.anchor?.qualifications?.length ? `<p>شروط مرتبطة بالاقتباس</p><ul>${
        item.anchor.qualifications.map(span => `<li dir="auto">${esc(span.quote)}</li>`).join('')
      }</ul>` : ''}
      <details><summary>تفاصيل الالتقاط</summary>
        ${item.capture_kind === 'publisher_snapshot' ? '<p>لقطة محفوظة من الناشر</p>' : ''}
        <p dir="auto">${esc(item.field || item.context)}</p><p dir="ltr">${esc(item.captured_at)}</p>
        ${item.body_sha256 ? `<code dir="ltr">${esc(item.body_sha256)}</code>` : ''}
      </details>
    </li>`).join('');
    const research = planner ? `<section class="v2-panel">
      <h2>قرار البحث</h2><p dir="auto">${esc(planner.question)}</p>
      ${planner.edition ? '<p>الاتجاهات محررة لهذا الموقع والهدف. التطبيق يطابق حقول المصدر قبل تجهيز الحزمة.</p>' : ''}
      <p dir="auto">${esc(planner.opportunity_hypothesis)}</p>
      <p id="v2-stop">${esc(label(planner.stop))} · ${esc(planner.page_calls)} زيارة</p>
      ${planner.missing_question ? `<p id="v2-plan-gap" dir="auto">${esc(planner.missing_question)}</p>` : ''}
      ${planner.conflicts?.map(item => `<blockquote dir="auto">${esc(item.quote)}
        ${link(item.page_url, item.page_url)}</blockquote>`).join('') || ''}
      <details><summary>خطوات البحث</summary>
        <ol id="v2-plan-trace" class="v2-items">${planner.trace.map(item => `<li>
          ${link(item.page_url, item.page_url)}
          <p>${esc(label(item.reason))} · ${esc(label(item.outcome))}${item.http_status ? ` · HTTP ${esc(item.http_status)}` : ""}</p>
          ${item.redirect_to ? link(item.redirect_to, item.redirect_to) : ""}
          ${item.observed_values?.map(value => `<blockquote dir="auto">${esc(value)}</blockquote>`).join('') || ""}
          ${item.gap_after ? `<p>${esc(label(item.gap_after))}</p>` : ''}
        </li>`).join('')}</ol>
      </details>
    </section>` : '';
    const claimsFor = option => option?.claims ? `<details>
      <summary>دليل كل حقل</summary>
      <ul>${option.claims.map(claim => `<li>${esc(fieldNames[claim.field])} · ${claim.evidence.map(index =>
        `<a href="#v2-source-${index}">${esc(evidence[index].scope)}</a>`).join('، ')}</li>`).join('')}</ul>
    </details>` : '';
    const alternatives = brief?.alternatives.map((item, i) => `<li><strong>${esc(item.name)}
      ${i === brief.strategy_index ? ' · الاختيار المقترح' : ''}</strong>
      <p dir="auto">${esc(item.plan)}</p><p dir="auto">${esc(item.tradeoff)}</p>
      ${planner ? `<p dir="auto">${esc(planner.options[i].audience)}</p>
        <p dir="auto">${link(planner.options[i].destination, planner.options[i].cta)} · ${esc(planner.options[i].metric)}</p>` : ''}
      </li>`).join('');
    const directions = brief?.directions.map((item, i) => `<li><strong>${esc(item.name)}
      ${i === brief.selected ? ' · الاتجاه الحالي' : ''}</strong>
      <p>${esc(item.rationale)}</p><p dir="auto">${esc(item.headline)} · ${esc(item.cta)}</p>
      ${planner?.edition ? `<p dir="auto">${esc(item.shot)}</p>${claimsFor(planner.options[i])}` : ""}
      ${i !== brief.selected && creative && !reason
        ? `<button type="button" class="adzy-btn flat" data-v2-direction="${i}">اختَر هذا الاتجاه</button>` : ''}
      </li>`).join('');
    const changes = proposal.changes.map(item => `<li>${esc(item.field)} · ${esc(label(item.before))} ← ${esc(label(item.after))}</li>`).join('');
    const shots = creative?.storyboard?.map(item => `<li><bdi>${esc(item.seconds)}</bdi> ثوانٍ
      <p>${esc(item.visual)}</p><p dir="auto">${esc(item.line)}</p></li>`).join('');
    const copyEditor = planner && brief ? `<details id="v2-copy-details">
      <summary>تعديل نص الاتجاه</summary>
      <label>العنوان<input name="headline" maxlength="55"></label>
      <label>الرسالة<input name="message" maxlength="105"></label>
      <label>الدعوة للفعل<input name="cta" maxlength="30"></label>
      <label>نص المنشور<textarea name="caption" maxlength="220" rows="3"></textarea></label>
      <div id="v2-reviewed-variants"></div>
      <p>لو الصياغة الجديدة تجاوزت حدود الدليل، التطبيق هيطلب مراجعتها.</p>
      <button type="button" class="adzy-btn flat" id="v2-copy-draft">نزّل المسودة لأداة اقتراحات الكتابة</button>
      <label>ملف اقتراحات الكتابة لهذه المسودة<input id="v2-copy-packet" type="file" accept=".json,application/json"></label>
      <p role="status" id="v2-copy-packet-status"></p>
      <div id="v2-copy-options"></div>
    </details>` : '';
    const editor = planner && brief && (!reason || reason === 'BRIEF_REVIEW_REQUIRED') ? `
      <form id="v2-brief-form">
        <label>فرضية الجمهور<input name="audience" value="${esc(brief.audience)}" minlength="4" maxlength="200" required></label>
        <label>النبرة<input name="tone" value="${esc(brief.tone)}" minlength="2" maxlength="75" required></label>
        <label>الاتجاه<select name="selected">${brief.directions.map((item, i) =>
          `<option value="${i}" ${i === brief.selected ? 'selected' : ''}>${esc(item.name)}</option>`).join('')}</select></label>
        ${copyEditor}
        <button class="adzy-btn blue" type="submit">احفظ الـbrief وجهّز الحزمة</button>
      </form>` : '';
    return `${research}
      <section class="v2-panel">
        <h2>البحث والأدلة</h2>
        ${brief ? `${planner?.edition ? "" : `<p dir="auto">${esc(brief.summary)}</p>`}
        <dl><dt>الجمهور المقترح</dt><dd>${esc(brief.audience)}</dd>
          <dt>السوق</dt><dd>${esc(brief.market)}</dd>
          ${brief.competitor ? `<dt>المنافس المقترح</dt><dd>${esc(brief.competitor)} · ${esc(brief.peer_rationale)}</dd>` : ''}</dl>` : ''}
        <ul class="v2-items">${sources}</ul>
        ${reason ? `<p id="v2-blocker">${esc(label(reason))}</p>` : ''}
        ${changes ? `<h3>أثر التعديل</h3><ul id="v2-changes">${changes}</ul>` : ''}
        <button type="button" class="adzy-btn flat" id="v2-refresh">أعد فحص الدليل</button>
      </section>
      ${brief ? `<section class="v2-panel">
        <h2>الاستراتيجية والـCreative brief</h2>
        <dl><dt>الهدف المقترح</dt><dd>${esc(brief.objective)}</dd>
          <dt>مقياس الحملة</dt><dd>${esc(brief.metric)}</dd>
          <dt>الافتراض</dt><dd>${esc(brief.assumption)}</dd>
          <dt>سبب الاختيار</dt><dd>${esc(brief.strategy_reason)}</dd>
          <dt>النبرة</dt><dd>${esc(brief.tone)}</dd></dl>
        <ul class="v2-items">${alternatives}</ul>
        <h3>الاتجاهات الإبداعية</h3>
        <ul class="v2-items">${directions}</ul>
        ${editor}
      </section>` : ''}
      ${creative && !reason ? `<section class="v2-panel${planner?.edition ? " v2-edition-preview" : ""}">
        <h2>معاينة الحملة</h2>
        <div class="v2-preview-layout">
          <div class="v2-preview-media">
            <div class="v2-actions v2-preview-formats" role="group" aria-label="مقاس معاينة البوستر">
              <button type="button" class="adzy-btn flat" data-v2-preview="portrait" aria-pressed="false">البوستر الطولي</button>
              <button type="button" class="adzy-btn flat" data-v2-preview="square" aria-pressed="false">البوستر المربّع</button>
            </div>
            <div id="v2-poster-preview"></div>
          </div>
          <div class="v2-preview-review">
            <article class="v2-creative v2-package-card theme-${esc(creative.theme)}" id="v2-package-card" dir="${esc(creative.direction)}">
              <div class="v2-creative-header" dir="auto">${esc(creative.brand_name)}</div>
              <h2>${esc(creative.copy.headline)}</h2><p>${esc(creative.copy.message)}</p>
              <p class="v2-creative-cta">${link(creative.source_url, creative.copy.cta)}</p>
            </article>
            <h3>نص المنشور</h3><p dir="auto">${esc(creative.caption)}</p>
            ${shots ? `<h3>سيناريو وStoryboard نصي</h3><ol class="v2-items">${shots}</ol>` : ""}
            <h3>مراجعة بشرية مطلوبة</h3>
            <ul>${brief.human_review.map(note => `<li>${esc(note)}</li>`).join('')}</ul>
            ${planner?.edition ? `
              ${brief.human_review.some(note => note.includes(disclosure)) ? '' : `<p>${esc(disclosure)}</p>`}
              ${claimsFor(planner.options[brief.selected])}` :
              '<p>التطبيق يطابق الاقتباس في HTML الصفحة. راجع دعم الاقتباس للنص الإعلاني قبل استخدامه.</p>'}
            <p>${approved ? 'التنزيل يستعيد الصورتين المعتمدتين.' :
              'اعرض المقاسين. الاعتماد يحفظ الصورتين اللي راجعتهم ويسمح بتنزيل الحزمة.'}</p>
            ${decision === 'rejected' ? '<p>رُفضت هذه النسخة.</p>' : ''}
            <div class="v2-actions">
              ${approved ? '<button type="button" class="adzy-btn blue" id="v2-download">نزّل حزمة المراجعة</button>'
                : '<button type="button" class="adzy-btn coral" id="v2-approve">اعتمد هذه النسخة</button>'}
              <button type="button" class="adzy-btn flat" id="v2-reject">ارفض هذه النسخة</button>
            </div>
          </div>
        </div>
      </section>` : ''}
    `;
  }

  async function packageFile(size) {
    if (busy || (!size && !state?.approved)) return;
    const seq = requestNumber;
    const run = state.run_id;
    const query = '?run_id=' + encodeURIComponent(run);
    const endpoint = size ? '/package/preview' + query + '&size=' + size : '/package/export' + query;
    const buttons = byId('v2-result').querySelectorAll('[data-v2-preview], #v2-download, #v2-approve');
    if (size) delete previews[size];
    for (const button of buttons) button.disabled = true;
    showStatus(size ? 'بنحمّل معاينة البوستر…' : 'بنحمّل الحزمة المعتمدة…');
    try {
      const response = await fetch(path(endpoint), {headers: window.authHeaders()});
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail?.code || 'EXPORT_FAILED');
      }
      const file = await response.blob();
      if (seq !== requestNumber) return;
      if (size) {
        const digest = response.headers.get('X-Poster-SHA256');
        if (!/^[a-f0-9]{64}$/.test(digest || '')) throw new Error('POSTER_ARTIFACT_UNAVAILABLE');
        const reader = new FileReader();
        reader.onload = () => {
          if (seq !== requestNumber) return;
          const poster = document.createElement('img');
          poster.alt = size === 'portrait' ? 'البوستر الطولي' : 'البوستر المربّع';
          poster.onload = () => {
            if (seq !== requestNumber || !poster.isConnected) return;
            previews[size] = digest;
            for (const button of byId('v2-result').querySelectorAll('[data-v2-preview]')) {
              button.setAttribute('aria-pressed', String(button.dataset.v2Preview === size));
            }
            const approve = byId('v2-approve');
            if (approve) approve.disabled = busy || !previews.portrait || !previews.square;
          };
          poster.src = reader.result;
          byId('v2-poster-preview').replaceChildren(poster);
        };
        reader.readAsDataURL(file);
      } else {
        const url = URL.createObjectURL(file);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'adzy-' + run + '.zip';
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      showStatus(size ? 'معاينة البوستر جاهزة.' : 'حزمة المراجعة جاهزة.');
    } catch (error) {
      if (seq === requestNumber) showStatus('تعذّر تحميل البوستر · ' + label(error.message));
    } finally {
      if (seq === requestNumber) {
        for (const button of buttons) button.disabled = busy;
        const approve = byId('v2-approve');
        if (approve) approve.disabled = busy || !previews.portrait || !previews.square;
      }
    }
  }

  function wireReview(root) {
    for (const control of root.querySelectorAll('button, input, select, textarea')) control.disabled = busy;
    byId('v2-refresh')?.addEventListener('click', () => submit('/refresh', {run_id: state.run_id}));
    const approve = byId('v2-approve');
    if (approve) {
      const needsPreviews = state.proposal.kind === 'SOURCE_CAMPAIGN';
      approve.disabled = busy || (needsPreviews && (!previews.portrait || !previews.square));
      approve.addEventListener('click', () => submit('/review', {
        run_id: state.run_id, decision: 'approved', ...(needsPreviews ? {artifact_sha256: previews} : {}),
      }));
    }
    byId('v2-reject')?.addEventListener('click', () => submit('/review', {run_id: state.run_id, decision: 'rejected'}));
  }

  function wireCopySuggestions(editor, keys) {
    const status = byId('v2-copy-packet-status');
    const panel = byId('v2-copy-options');
    const limits = {headline: 55, message: 105, cta: 30, caption: 220};
    const names = {headline: 'العنوان', message: 'الرسالة', cta: 'الدعوة للفعل', caption: 'نص المنشور'};
    byId('v2-copy-draft').onclick = () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(state)], {type: 'application/json'}));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'campaign-draft.json';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    byId('v2-copy-packet').onchange = async event => {
      const seq = requestNumber;
      const file = event.currentTarget.files[0];
      panel.replaceChildren();
      status.textContent = '';
      try {
        if (!file || file.size > 32000) throw new Error('invalid');
        const packet = JSON.parse(await file.text());
        if (seq !== requestNumber || !editor.isConnected) return;
        if (packet.run_id !== state.run_id || packet.proposal_sha256 !== state.proposal.sha256) {
          status.textContent = 'الاقتراحات دي تخص نسخة تانية من الـbrief.';
          return;
        }
        if (!Array.isArray(packet.directions) || packet.directions.length !== 3 ||
            packet.directions.some(option => !option || typeof option.angle !== 'string' ||
              keys.some(key => typeof option[key] !== 'string') ||
              !Number.isInteger(option.evidence_index) || !state.proposal.evidence[option.evidence_index])) {
          throw new Error('invalid');
        }
        for (const option of packet.directions) {
          const section = document.createElement('details');
          const title = document.createElement('summary');
          title.textContent = option.angle;
          section.append(title);
          const quote = document.createElement('blockquote');
          quote.dir = 'auto';
          quote.textContent = state.proposal.evidence[option.evidence_index].quote;
          section.append(quote);
          for (const key of keys) {
            const row = document.createElement('p');
            row.dir = 'auto';
            row.textContent = names[key] + ' · ' + option[key];
            const select = document.createElement('button');
            select.type = 'button';
            select.textContent = 'جرّب الحقل';
            select.dataset.copyField = key;
            select.disabled = option[key].length > limits[key];
            select.onclick = () => { editor.elements[key].value = option[key]; };
            section.append(row, select);
          }
          panel.append(section);
        }
      } catch (_) {
        if (seq === requestNumber && editor.isConnected) status.textContent = 'ملف اقتراحات الكتابة غير صالح.';
      }
    };
  }

  function render() {
    clearTimeout(timer);
    byId('v2-plan-form').querySelector('button').disabled = busy;
    const form = byId('v2-form');
    form.querySelector('button').disabled = busy || !state;
    byId('v2-package-form').querySelector('button').disabled = busy || !state;
    const root = byId('v2-result');
    if (!state?.proposal) {
      root.innerHTML = '';
      return;
    }
    const {proposal, approved, reason, decision} = state;
    if (proposal.kind === 'SOURCE_CAMPAIGN') {
      root.innerHTML = packagePanel(proposal, approved, reason, decision);
      wireReview(root);
      const editor = byId('v2-brief-form');
      if (editor) {
        editor.dataset.run = state.run_id;
        const keys = ['headline', 'message', 'cta', 'caption'];
        const syncCopy = () => {
          const direction = proposal.brief.directions[Number(editor.elements.selected.value)];
          for (const key of keys) editor.elements[key].value = direction[key];
          const variants = byId('v2-reviewed-variants');
          variants.replaceChildren();
          const reviewed = proposal.planner?.options[Number(editor.elements.selected.value)]?.variants;
          for (const [field, values] of Object.entries(reviewed || {})) {
            for (const value of values) {
              if (value === direction[field]) continue;
              const button = document.createElement('button');
              button.type = 'button';
              button.className = 'adzy-btn flat';
              button.disabled = busy;
              button.textContent = value;
              button.addEventListener('click', () => {
                editor.elements[field].value = value;
                editor.elements[field].removeAttribute('aria-invalid');
              });
              variants.append(button);
            }
          }
        };
        syncCopy();
        wireCopySuggestions(editor, keys);
        editor.elements.selected.addEventListener('change', syncCopy);
        editor.addEventListener('input', event => event.target.removeAttribute('aria-invalid'));
        editor.onsubmit = event => {
          event.preventDefault();
          const fields = new FormData(editor);
          const selected = Number(fields.get('selected'));
          const copy = Object.fromEntries(keys.map(key => [key, fields.get(key)]));
          const changed = keys.some(key => copy[key] !== proposal.brief.directions[selected][key]);
          submit('/brief', {run_id: state.run_id, audience: fields.get('audience'),
                            tone: fields.get('tone'), selected, ...(changed ? {copy_edit: copy} : {})});
        };
      }
      byId('v2-download')?.addEventListener('click', () => packageFile());
      for (const preview of root.querySelectorAll('[data-v2-preview]')) {
        preview.addEventListener('click', () => packageFile(preview.dataset.v2Preview));
      }
      for (const choice of root.querySelectorAll('[data-v2-direction]')) {
        choice.addEventListener('click', () => submit('/direction', {
          run_id: state.run_id, selected: Number(choice.dataset.v2Direction),
        }));
      }
      if (state.recheck_at && !busy) scheduleCheck(Date.parse(state.recheck_at) - Date.parse(state.checked_at));
      return;
    }
    const offer = proposal.offer;
    const trace = proposal.research.trace.map(row =>
      `<li>${link(row.page_url, row.page_url)} <span>${esc(label(row.reason))} · ${esc(label(row.outcome))}</span></li>`
    ).join('');
    const changes = proposal.changes.map(row => `<li><strong>${esc(row.affects)}</strong>
      <p><bdi>${esc(label(row.before))}</bdi> ← <bdi>${esc(label(row.after))}</bdi></p>
      ${row.observed_excerpt ? `<blockquote>${esc(row.observed_excerpt)}</blockquote>` : ''}</li>`).join('');
    const plan = proposal.strategy;
    const copy = proposal.creative?.copy;
    const eligible = !!plan && !reason;
    const pending = eligible && !approved;
    root.innerHTML = `
      <section class="v2-panel">
        <h2>قرار البحث</h2>
        <p id="v2-question">${esc(proposal.research.questions.at(-1))}</p>
        <p id="v2-stop">${esc(label(proposal.research.status))}</p>
        <ol id="v2-trace">${trace}</ol>
        ${reason ? `<p id="v2-blocker">${esc(label(reason))}</p>` : ''}
        ${proposal.research.next_action ? '<p>أضف رابط صفحة مخصصة لشروط العرض وأعد البحث.</p>' : ''}
        ${changes ? `<h3>ما الذي تغيّر؟</h3><ul id="v2-changes">${changes}</ul>` : ''}
        ${proposal.watch_url ? '<button type="button" class="adzy-btn flat" id="v2-refresh">أعد فحص الدليل</button>' : ''}
      </section>
      ${plan ? `<section class="v2-panel" id="v2-strategy">
        <h2>الاستراتيجية المقترحة</h2>
        <dl>
          <dt>الهدف</dt><dd>${esc(proposal.goal)}</dd>
          <dt>التوصية</dt><dd>${esc(plan.recommendation)}</dd>
          <dt>الافتراض</dt><dd>${esc(plan.assumption)}</dd>
          <dt>البديل</dt><dd>${esc(plan.alternative)} ${esc(plan.tradeoff)}</dd>
          <dt>مقياس الحملة</dt><dd>${esc(plan.metric)}${proposal.booking_target ? ` · الهدف ${esc(proposal.booking_target)} حجزًا` : ''}</dd>
        </dl>
        <details open><summary>دليل العرض${offer.source_kind === 'synthetic_fixture' ? ' · مصدر مصطنع للديمو' : ''}</summary>
          <p>${link(offer.page_url, offer.page_url)}</p>
          <p><bdi>${esc(copy.price)}</bdi> · ${esc(copy.branch)}</p>
          <blockquote>${esc(copy.terms)}</blockquote>
          <p>ساري حتى <bdi>${esc(copy.validity)}</bdi></p>
          <details><summary>نسخة المصدر ووقت التقاطه</summary><code dir="ltr">${esc(offer.sha256)}</code><p dir="ltr">${esc(offer.captured_at)}</p></details>
        </details>
      </section>` : ''}
      ${pending ? `<section class="v2-panel">
        <h2>النص المقترح للمراجعة</h2>
        <p>${esc(copy.headline)} · ${esc(copy.cta)}</p>
        ${decision === 'rejected' ? '<p>رُفضت هذه النسخة.</p>' : ''}
        <div class="v2-actions"><button type="button" class="adzy-btn coral" id="v2-approve">اعتمد هذه النسخة</button>
        <button type="button" class="adzy-btn flat" id="v2-reject">ارفض هذه النسخة</button></div>
      </section>` : ''}
      ${approved && !busy ? card(proposal) : ''}`;
    wireReview(root);
    if (approved && !busy) scheduleCheck(Date.parse(offer.expires_at) - Date.parse(state.checked_at));
  }

  function scheduleCheck(remaining) {
    if (Number.isFinite(remaining)) {
      timer = setTimeout(() => {
        if (window.Adzy?.stage === 'campaign') load();
      }, Math.min(2147483647, Math.max(0, remaining)));
    }
  }

  async function load() {
    const seq = ++requestNumber;
    previews = {};
    busy = true;
    render();
    showStatus('بنحمّل نسخة الحملة…');
    try {
      const next = await window.api('GET', path());
      if (seq !== requestNumber) return;
      state = next;
      showStatus(state.approved ? 'تم اعتماد نسخة الحملة الحالية.' : '');
    } catch (_) {
      if (seq !== requestNumber) return;
      state = null;
      showStatus('تعذّر تحميل الحملة.');
    } finally {
      if (seq === requestNumber) {
        busy = false;
        render();
      }
    }
  }

  async function submit(suffix, body) {
    if (busy || !state) return;
    const seq = ++requestNumber;
    busy = true;
    render();
    showStatus(suffix === '/review' ? 'بنسجّل قرارك…'
      : suffix === '/brief' ? 'بنراجع النص وبنحفظ التعديل…' : 'بنبحث في صفحات المصدر…');
    try {
      const next = await window.api('POST', path(suffix), body);
      if (seq !== requestNumber) return;
      const unchanged = suffix === '/refresh' && next.run_id === state.run_id;
      if (next.run_id !== state.run_id || next.approval_id !== state.approval_id) previews = {};
      state = next;
      showStatus(unchanged ? 'بصمة الدليل تطابق النسخة المحفوظة.'
        : state.approved ? 'تم اعتماد نسخة الحملة الحالية.' : 'حُفظت نتيجة النسخة الحالية.');
    } catch (error) {
      if (seq !== requestNumber) return;
      const code = errorCode(error);
      if (error.status === 409 && code !== 'COPY_REQUIRES_EVIDENCE_REVIEW') await load();
      if (code === 'COPY_REQUIRES_EVIDENCE_REVIEW' && body.copy_edit) {
        busy = false;
        render();
        const editor = byId('v2-brief-form');
        if (editor) {
          editor.elements.selected.value = String(body.selected);
          editor.elements.audience.value = body.audience;
          editor.elements.tone.value = body.tone;
          editor.elements.selected.dispatchEvent(new Event('change'));
          for (const [key, value] of Object.entries(body.copy_edit)) editor.elements[key].value = value;
          for (const key of errorDetail(error)?.fields || []) editor.elements[key]?.setAttribute('aria-invalid', 'true');
          byId('v2-copy-details').open = true;
        }
      }
      const fields = {headline:'العنوان', message:'الرسالة', cta:'الدعوة', caption:'نص المنشور'};
      const invalid = (errorDetail(error)?.fields || []).map(field => fields[field]).filter(Boolean);
      showStatus(code ? label(code) + (invalid.length ? ' الحقول المعنية: ' + invalid.join('، ') : '') : 'تعذّر إكمال الطلب.');
    } finally {
      if (seq === requestNumber && busy) {
        busy = false;
        render();
      }
    }
  }

  function errorDetail(error) {
    try { return JSON.parse(error.message); } catch (_) { return null; }
  }

  function errorCode(error) {
    return errorDetail(error)?.code || null;
  }

  async function startPlan(event) {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    const input = {url: fields.get('url').trim(), objective: fields.get('objective').trim(), language: fields.get('language')};
    const key = JSON.stringify(input);
    if (lastPlan?.key !== key) lastPlan = {key, id: crypto.randomUUID()};
    const seq = ++requestNumber;
    busy = true;
    render();
    showStatus('بنقرأ المصادر العامة ونجهّز قرار الحملة…');
    try {
      const {run_id: expected} = await window.api('GET', '/api/v2/campaigns/planning-state?url=' + encodeURIComponent(input.url));
      if (seq !== requestNumber) return;
      const next = await window.api('POST', '/api/v2/campaigns/plan', {
        ...input, request_id: lastPlan.id, expected_run_id: expected,
      });
      if (seq !== requestNumber) return;
      lastPlan = null;
      await window.adzyLoadCampaignBrand(next.domain_key);
    } catch (error) {
      if (seq === requestNumber) showStatus(label(errorCode(error)) || 'تعذّر إنشاء مسودة الحملة.');
    } finally {
      if (seq === requestNumber) {
        busy = false;
        render();
      }
    }
  }

  window.adzyCampaign = {
    reset() {
      requestNumber += 1;
      busy = true;
      state = null;
      render();
      byId('v2-brand').textContent = '';
      showStatus('بنحمّل البراند…');
    },
    async open(nextBrand) {
      const planner = byId('v2-plan-form');
      planner.onsubmit = startPlan;
      if (!nextBrand) {
        brand = null;
        state = null;
        busy = false;
        byId('v2-brand').textContent = '';
        showStatus('');
        render();
        return;
      }
      const form = byId('v2-form');
      if (brand?.domain_key !== nextBrand.domain_key) {
        form.reset();
        byId('v2-package-form').reset();
      }
      brand = nextBrand;
      state = null;
      byId('v2-brand').textContent = nextBrand.organization_name || nextBrand.name || nextBrand.domain_key;
      form.onsubmit = event => {
        event.preventDefault();
        const fields = new FormData(form);
        submit('', {expected_run_id: state?.run_id ?? null, goal: fields.get('goal'),
                    booking_target: fields.get('booking_target') ? Number(fields.get('booking_target')) : null,
                    offer_name: fields.get('offer_name'), terms_url: fields.get('terms_url') || null});
      };
      byId('v2-package-form').onsubmit = async event => {
        event.preventDefault();
        const seq = requestNumber;
        const file = new FormData(event.currentTarget).get('brief');
        try {
          if (!file || file.size > 64000) throw new Error('Invalid brief file');
          const brief = JSON.parse(await file.text());
          if (seq === requestNumber) submit('/package', {expected_run_id: state.run_id, brief});
        } catch (_) {
          if (seq === requestNumber) showStatus('اختَر ملف Creative brief صالحًا وأصغر من 64 كيلوبايت.');
        }
      };
      await load();
      if (brand === nextBrand && state?.proposal?.planner) {
        const {url, objective, language} = state.proposal.planner.input;
        planner.elements.url.value = url;
        planner.elements.objective.value = objective;
        planner.elements.language.value = language;
      }
      if (brand === nextBrand && state?.proposal && state.proposal.kind !== 'SOURCE_CAMPAIGN') {
        form.elements.goal.value ||= 'TRIAL_BOOKINGS';
        form.elements.booking_target.value ||= state.proposal.booking_target ?? '';
        form.elements.offer_name.value ||= state.proposal.offer_name;
      }
    },
  };
})();
