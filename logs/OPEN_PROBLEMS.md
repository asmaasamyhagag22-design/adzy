# 🗂️ OPEN PROBLEMS — سجل المشاكل المؤكَّدة المفتوحة

> **تصحيح سجل 2026-07-19 (REGISTER-TAG-DRIFT):** تاجات ✅ اتراجعت ضد الكود: CS-44/CS-63 كانت معلَّمة fix ✅ زوراً — **اتصلحوا فعلاً النهارده** (dedup+salience في trends). CS-26/29/30 disclose ✅ الزائف — **اتصلحوا فعلاً النهارده**. CS-121/CS-54/70/CS-91/CS-116 — **مقفولين النهارده**. CS-02-oneshot residual — **مقفول** (compliance في oneshot). P-48 كان متصلح ومتعلّم غلط كمفتوح. المرجع الموثوق: process.md §8.5 + الكود نفسه.


**آخر تحديث:** 2026-07-18 · **المصدر:** دمج `logs/problems.md` (السجل) + `logs/sweep/refutation_final.md`
(طبقة التكذيب العدائية على 120 اتهام: 71 مؤكَّد / 49 مكذَّب).

**إزاي اتعملت القايمة دي:**
- **استُبعد** كل اللي اتصلّح (عناقيد 1-7/9/10 · CS-01/03/05 يوم 07-17 · CS-02/04/62 يوم 07-18).
- **استُبعد** كل اللي **اتكذّب** (49 بند — مش عيوب حقيقية؛ القايمة تحت في آخر الملف).
- **اتساب** المؤكَّد + المفتوح بس. الحدة = **الحدة المصحّحة من المُكذِّب** (كتير اتخفّض).
- بند بيتكرر برقمين (المسح الأصلي + الـ120) اتدمج في صف واحد.

**العدد:** ~**98 بند مؤكَّد مفتوح** (بدون الريل) · ~110 بالريل المؤجّل.
**تم إصلاحه 2026-07-18 (Batch A):** CS-08 · CS-09/100 · CS-104 · CS-10/18 · CS-16/23 · CS-17 (شوف تحت).
**اللغة:** `fix` = إصلاح كود · `disclose` = يتفصح (ضعف مُفصَح درع) · `decide` = قرار المالكة، مش تقني.
**★** = المالكة عيّنته للدفعة الجاية · **cl.N** = عنقود HANDOFF رقم N.

---

## 🎯 TOP 10 بالأثر (مؤكَّد، مفتوح)

| # | البند | المكان | الحدة | يوصل العميل؟ |
|---|---|---|---|---|
| 1 | **CS-46** — الـLedger بيختم superlative مفبرك كـ"verified" tier=brand على **12/18 بروفايل** (وعد الموات مسرحية) | `grounding/ledger.py:688` | crit→high | ✅ |
| 2 | **P-48** — الديليفرابل النهائي (launch bundle U6) **عمره ما اتنتج**؛ الـCLI مش بياخد studio runs | `launch_bundle/__main__.py:56` | 🔴 crit | ✅ |
| 3 | **CS-08** — ضمان "Provable Brand Safety" بيتطبع على **وجود الملف** مش حكم audit (0/10 sidecars) | `dashboard/build.py:1196` | high | ✅ ★ |
| 4 | **CS-09/100** — 78% من نسب "peer fit" هي **1÷ترتيب-SERP** ملبوسة "تطابق"؛ `basis` مابيتقراش | `dashboard/build.py:148,163` | high→med | ✅ ★ |
| 5 | **CS-49** — deep_search بينسب سوشيال **شركات تانية + شخص خاص** للبراند | `scraper/deep_search.py:89` | crit→high | داخلي |
| 6 | **CS-48** — SSRF بعد redirect في lite-scraper (بيتحقق من URL قبل الـ302 بس) | `competitor/lite_scrape.py:48` | crit→high | داخلي |
| 7 | **CS-75** — ادعاءات الاعتماد/الجوايز **subject-ungated**: "معتمد من X" بيطابق أي سترنج اعتماد | `grounding/ledger.py:311` | high | ✅ |
| 8 | **P-93** — Policy Linter **ar/en بس** → verdict=PASS ("متوافق") لكل براند بلغة تانية | `policy_linter/rules.py:51` | high | ✅ |
| 9 | **CS-45** — إثبات two-phase E2E **بيتخطى في السويت + بيفشل standalone**؛ الكومنت بيقول العكس | `tests/test_two_phase_e2e.py:57` | crit→high | (اختبارات) ★cl.13 |
| 10 | **CS-104 + CS-10** — عدّاد Products غلط 22/90 براند · حارس `/ask` بيفشل مفتوح عبر اللغات | `products.py:314` · `archive_qa.py:434` | med/high | ✅ ★ |

---

## 🟥 GROUNDING / LEDGER / COMPLIANCE (سطح الثقة الأعلى)

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-46 | superlative مفبرك = tier=brand على 12/18 | `grounding/ledger.py` + `poster/audit.py:67` | crit→high | **✅ اتقفل (حكم المالكة + judge)** |
| CS-75 | ادعاء اعتماد/جايزة subject-ungated | `grounding/ledger.py:311` | high | **✅ اتقفل (certification+award subject-gated + judge)** |

> **✅ CS-46 + CS-75 — اتقفلوا بحكم المالكة (2026-07-18):** الحكم: **الدليل الأضيق من الادعاء = مرفوض**
> (لا verify)، و**الاعتماد لازم يسمّي الجهة الحقيقية**. المالكة وافقت على توصيل الـjudge (Flash،
> ~$0.001/claim). التنفيذ: (1) `certification`+`award` اتضافوا لـ`_SUBJECT_GROUPS` — آمن، مسار
> no-judge يفضل lenient فمافيش كسر · (2) الـjudge اتوصّل في `poster/audit.py:build_poster_audit`
> (زي `poster/concept.py:233` بالفعل) — guarded، أي فشل/403 → lenient مش crash. مُثبَت hermetic
> بـmock judge (`tests/test_ledger_judge_wired.py`): "الأسرع في السوق"←"الأسرع في توصيل الطرود"
> **مرفوض**، "معتمد من هارفارد"←"معتمد من المجلس" **مرفوض**، والمطابق بيعدّي. الـxfail باقٍ (بيوثّق
> مسار الـunit بلا judge). **الـFlash الحي يتأكّد على بيئة المالكة (هنا 403).**
> **تذكرة صغيرة اكتشفتها:** ضمير مشترك زي "نحن" بيخلّي الـsubjects مش متباعدة فالـjudge مايتسألش —
> فجوة تغطية stopwords، منفصلة عن CS-46.
| CS-122 | Sale/Offer مش hard claim | `grounding/ledger.py` | med | **⚠ محتاج قرار (two-truth-domains)** |
| P-85 / CS-103 / CS-12 | `evidence_count` = حجم الفهرس مش دعم الادعاء؛ الفرونت "checked against N evidence items" | `frontend/…poster-studio-card.tsx:472` | med→high | **✅ اتصلّح (relabel)** |
| CS-121 | الكالندر **مستحيل ياخد compliance sheet** — 11/11 audit بيرجّعوا None | `grounding/compliance.py:59` | med | fix (mapping) |

> **P-85 ✅** — الليبل بقى *"matched against a N-snippet evidence index"* (بدل "checked against N
> evidence items") — بيوضّح N = حجم الفهرس، والمقياس الحقيقي "{claims_verified} verified" فوقه.
> **CS-122 محتاج قرارك (زي CS-46):** sale/offer كـclaims محتاجة دليل موقع **بتكسر بوسترات الحملة اللي
> بتروّج عرض** قرّره الميديا باير. two-truth-domains: عرض حملة جديد = **قرار تصميم مش حقيقة**. السؤال:
> العرض الترويجي حقيقة-تتأسّس ولا قرار-حملة؟
> **CS-121:** mapping دقيق (`items` الكالندر مقابل `final_copy.fields` البوستر) — مخاطرة على sheet
> البوستر، بعناية بميزانية طازة.
| P-101 | السويت **بيشهد على الfail-open** ("best coffee"←"best regards" اتأكد SOURCED) | `tests/test_ledger_subject_judge.py:51` | high | fix ★cl.13 |

## 🟧 DASHBOARD / FRONTEND (الديليفرابل المرئي)

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-08 | ضمان brand-safety على وجود الملف مش حكم audit؛ 0/10 sidecars | `dashboard/build.py:1196` | high | fix ✅★ |
| CS-09=100=P-30=P-80 | 1÷ترتيب-SERP معروض "N% · peer fit"؛ `basis` مابيتقراش | `dashboard/build.py:148,163` | high→med | fix ✅★ |
| CS-104 | عدّاد "Products" بقاعدة مختلفة عن الـpicker → 22/90 = 0 غلط | `dashboard/products.py:314` | med | fix ✅★ |
| CS-10 | حارس دعم إجابة archive_qa بيفشل مفتوح عبر اللغات (المسار الغالب) | `dashboard/archive_qa.py:434` | high→med | fix ✅★ |
| CS-18 | backstop archive_qa أعمى للأرقام/السنين/الأسعار | `dashboard/archive_qa.py:429` | med | fix ✅ |
| CS-16=23 | XSS `javascript:`/`data:` في href (منافس + SERP chip)؛ escape بس بدون allow-list | `dashboard/build.py:147,597` | med | fix ✅ |
| CS-17 | repr خام (`AudienceType.MIXED`/`EvidencedField`) بيتسرّب للعميل | `dashboard/build.py:440` | med | fix ✅ |
| CS-24 | كوبي الداشبورد المتحطوط عامية مصرية + `lang=ar dir=rtl` قسري لكل بلد | `dashboard/build.py:470,1229` | med | fix/decide ✅ |
| CS-07=11 | studio cards بتقرا `NEXT_PUBLIC_API_BASE_URL` مش معرّف → hardcode `127.0.0.1:8000` | `poster-studio-card.tsx:129` · `reel-studio-card.tsx:33` | high/med | fix ✅(deploy) |
| CS-30 | hero/footer بيأكدوا تغطية Ledger مطلقة البوابة مابتضمنهاش | `dashboard/build.py:1221,1211` | low | disclose ✅ |
| CS-26 | كوبي الاستوديو بيوعد ضمانات بتناقضها الـartifacts | `poster-studio-card.tsx:210` | low | disclose ✅ |
| CS-29 | caption الريل بيـhardcode "Veo 3.1" مهما كان الموديل | `dashboard/build.py:1190` | low | disclose ✅ |

> **CS-03 (/ask)** — **اتصلّح 07-17** (`_PAID_GET_ROUTES` بيحرس `/ask`). residual (rate-limit/طول q/`strong=False`) = دفاع عمقي، مش عيب مفتوح.

## 🟨 POSTER / GATES / POLICY-LINTER

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| P-93 | Policy Linter ar/en بس → PASS لكل براند بلغة تانية | `policy_linter/rules.py:51-84` | high | fix ✅ |
| CS-13=105=P-08 | 3 جداول ccTLD بتنحرف؛ art_director 18-MENA ⊂ locale 63؛ المسار الكلاسيكي بياخد الضعيف | `poster/art_director.py:582` | med | fix ✅ |
| P-25 | vision_qa: `has_latin_text` و`score>=7` موعودين كشروط pass بس **مش re-ANDed** | `poster/vision_qa.py:166` | med | fix ✅ ★cl.8 |
| P-26 | GR-1 الإنجليزي بيفوت "guaranteed profit"؛ فئة طبية مجهولة → default بيتخطى القواعد الطبية | `policy_linter/rules.py:62` | med | fix ✅ ★cl.8 |
| CS-15 | ماب الامتثال مفاتيحه سترنجز مش BusinessCategory → يفشل مفتوح للطبي-المجاور | `poster/contracts.py:66` | med→low | disclose ✅ |
| CS-14 | نص مكشوط خام في برومبت البوستر بلا تحييد (P-72 ماغطّاش دول) | `poster/concept.py:367` | med→low | disclose |
| poster-oneshot | **residual CS-02:** محرك oneshot (`build_oneshot_prompt`) مفيهوش امتثال | `poster/oneshot.py:55` | med | fix |
| P-44 | تايبوجرافي Latin مكسورة في الـHTML overlay (≥2 براند) | `poster/template.py:343` | high | disclose ✅(render) |
| P-45 | منتج بطل مفبرك بنص-وهمي مكان صور حقيقية | poster default path | high | disclose ✅ |
| P-46 | وردمارك البراند نفسه مشوّه ("vodafona") | poster | med | disclose ✅ |
| P-47 | فجوات QA لغوية/هوية (شدة ناقصة، برتقالي→أزرق، تنافر لغة) | poster | med | disclose ✅ |
| P-35 | digilians: شهادات نص-وهمي + RTL bidi بيحرّك السلاش في URL | outputs | med | fix ✅ |
| P-36 | marasim: panel رسم حواف حادة فوق مشهد off-brief، مفيش sidecar | outputs | med | fix ✅ |

## 🟦 STRATEGY (الكالندر)

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| P-10 | تجويع البرومبت: الكالندر مايشوفش SWOT/منافسين/الهدف/الأسعار/البلد | `strategy/builder.py:132` | high | fix ✅ |
| P-11 | `ready_for_strategy:false` بيتجاهل (verdict محسوب، متخزّن، مايتقراش) | `dashboard/run.py:115` | high | fix/decide ✅ |
| P-12 | الترند/المواسم عمرها ما توصل كالندر حي (studio بيشيل `--trends`) | `dashboard/run.py:116` | high | fix ✅ |
| P-14=CS-85 | cadence قالب واحد=4؛ نفس tuple الأوفست؛ مفيش config بالفئة | `strategy/builder.py:315` | high | fix ✅ |
| P-15 | فاضي تجارياً: 0% عروض، 12% CTA على 108 بند؛ 88% TOFU وخطة تقول LEADS/SALES | `strategy/builder.py` | high | fix ✅ |
| P-16 | معمارية الهدف: الجوال مايتسألش؛ هدف واحد 100% ميزانية، `num_ad_sets=1` | `media_plan/schemas.py:46` | high | decide (D-2) ✅ |
| P-19 | انحراف factual الـLedger مايمسكهوش (NTI "للطلاب" ضد الدليل) | outputs | high | disclose ✅ |
| CS-21 | `channel_weights` زينة: الداش FB55%/TikTok5%، الكالندر بيشحن 25/25/25/25 | `strategy/builder.py:271` | med→low | fix ✅ |
| CS-22 | LinkedIn غير قابل للوصول في fallback بلا سوشيال + غير قابل للترتيب | `strategy/builder.py:77` | med→low | fix ✅ |
| P-17 | Meta-only serialization غير مُفصَح | strategy/media_plan | med | disclose ✅ |
| P-71 | `ReadinessReport` محسوب، محدش بيستهلكه؛ نسختين متضاربتين | `quality.py:198`/`readiness.py:252` | — | decide (D-6) |

## 🟪 LAUNCH BUNDLE (عنقود 11 = الديليفرابل النهائي)

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| P-48 | U6 عمره ما أنتج bundle؛ CLI مش بياخد studio runs؛ xlsx بيتخفّض CSV صامت | `launch_bundle/__main__.py:56` | 🔴 crit | fix ✅★ |
| P-49 | الخطة مش بتتستورد Ads Manager (7/17 عمود مش Meta) **[UNVERIFIED — مصادر ثانوية]** | `campaign_plan.py:2` | high | fix/disclose ✅★ |
| P-50 | approval theater: token محسوب بس مفيش كود بيتحقق منه/يقلب DRAFT→APPROVED | `schemas.py:91` | high | fix ✅★ |
| P-51 | الشيت مايبرّفش buyer: بلا بلد/تليفون/فلوس مطلقة، repr-leak audience | `schemas.py:13` | high | fix ✅★ |
| P-52 | بوابات فاضية: `variation_fn`→`{}` بيعدّي؛ compliance بيكتب فوق creative؛ `{}` compliance.json | `loop.py:80`/`bundle.py:66` | med | fix ✅★ |
| P-53 | معادي لإكسل: CSV injection (`=+-@`)، `\r\r\n` صفوف مزدوجة، UTF-8 بلا BOM | `bundle.py:90`/`ads_export.py:25` | med | fix ✅★ |

## ⚙️ SCRAPER CORE

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-49 | deep_search بينسب سوشيال غير + شخص خاص للبراند | `scraper/deep_search.py:89` | crit→high | fix ✅ |
| CS-79 | Ajax frontier بيتخطى same-host → off-site يتكشط كأنه البراند | `scraper/crawler.py:933` | high→med | fix |
| CS-56 | next_data inferred_internal **بيتكشط** رغم عقد "مايتجابش"؛ synthesis = قالب Buffalo Burger مفرد | `scraper/crawler.py:576` | high→med | fix |
| CS-93 | deep_search `_social_platform` substring host match (netflix/fedex غلط) | `scraper/deep_search.py:48` | med | fix |
| CS-124 | `_brand_guess` بياخد أقصى يسار الدومين → subdomained بيدوّر توكن بلا معنى | `scraper/deep_search.py:34` | med | fix |
| CS-94 | frontier dedup بمفتاحين (`dedup_key` vs `normalize_url`) → variants تتجاب مرتين | `scraper/crawler.py:909` | med | fix |
| CS-19 | robots.txt بيتجاب مرتين (placeholder + real، بلا memoization) | `scraper/crawler.py:688` | med→low | fix |
| CS-125 | كومنت post-redirect بيدّعي 3 collapses؛ `normalize_url` بيعمل 1 | `scraper/crawler.py:378` | med→low | fix |
| CS-158 | EN-first Accept-Language + `locale=en-US` متحطوط لكل براند | `scraper/config.py:108` | low | verify-first |
| CS-159 | `extract_forms` بيمسح نفس الsiblings 3× ومايبصش لـancestor | `scraper/extractors/forms.py:36` | low | fix |

## 🔌 API / JOBS

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-80=98 | traceback كامل + استثناء خام على route `/api/jobs/{id}` غير موثّق | `api/jobs/runner.py:318` | high→med | fix |
| CS-82=99 | Queue واحدة/job وN مشتركين SSE → المشاهدين بيتقاسموا الستريم، الخاسر بيعلّق | `api/routes/jobs.py:118` | high→med | fix |
| CS-81 | نجاح صامت: 3 من 4 مجموعات LLM تفشل، الjob SUCCEEDED بلا تحذير | `api/jobs/runner.py:224` | high→low | fix |
| CS-83=97 | TTL cleanup **مستحيل** يطرد job غير منتهي → QUEUED/RUNNING عالق يتسرّب | `api/jobs/store.py:110` | high→low | fix |
| CS-127 | `JOB_TTL_SECONDS=0` بيحوّل الcleanup لبزي-سبين بيعلّق الloop | `api/jobs/store.py:107` | med | fix |
| CS-128 | استثناء واحد بيعطّل الcleanup للأبد | `api/jobs/store.py:121` | med | fix |
| CS-96=126 | `has_llm()` بيقبل `OPENAI_API_KEY` والبايبلاين Gemini بس → fail-fast بيعدّي لكونفيج مايقدرش يخدمه | `api/jobs/runner.py:337` | med→low | fix |

## 🧪 TESTS-INTEGRITY (عنقود 13)

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-45 | two-phase E2E بيتخطى في السويت + بيفشل standalone؛ الكومنت بيقول العكس | `tests/test_two_phase_e2e.py:57` | crit→high | fix ★cl.13 |
| CS-54=70 | two-phase carve-out متختبر ضد نسخة معاد تنفيذها من الclosure | `tests/test_fetcher_two_phase.py:30` | high→med | fix |
| CS-55 | `test_leaf_detail...` بيـgrep السورس `'+ (3 if'` بس، عمره ما بيسكور block | `tests/test_evidence_pack.py:559` | high→med | fix |
| CS-92=118 | اختبار spend-tripwire بيرضى بسطر الimport لوحده؛ 5 مسارات مدفوعة تانية مغطّاش | `tests/test_spend_tripwire.py:57` | med→low | fix |
| CS-91 | اختبار "التقوية" بتاع P-79 لسه source-grep | `tests/test_competitor_registry.py:132` | med | disclose |
| P-103=119 | G2 calibration "إلزامي" بيشتغل تحت `RUN_LIVE_GATES` بس؛ بديل CI بيفحص PNG>100KB | `tests/test_render3_g2_calibration.py:25` | low | disclose ★cl.13 |
| CS-120=137 | `test_concept` بيـgrep سورس الموديول مش البرومبت المبني (**متنازَع** — run كذّبه) | `tests/test_concept.py:138` | low | fix |

## 📏 MEASUREMENT-SCRIPTS

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-51=69 | `CAP=12` مقابل 40/50 حقيقي → 92% من labels الـCAP كاذبة | `benchmark/measure_ecom_coverage.py:23` | high→med | fix |
| CS-52 | "SPEED هي الرافعة" من budget 150s وهمي هو فعلياً 540/560s | `benchmark/measure_ecom_bottleneck.py:81` | high→med | fix |
| CS-53=68 | `measure_step1/2/reel` بتقيس 3 من 6 بروفايلات صامت وبتخرج 0 "نجاح" | `grounding/measure_step1.py:35` | high→med | fix |
| CS-67 | كشطات فاضية بتاخد 100% coverage (درجة كاملة للي مفيهوش محتوى) | `benchmark/measure_evidence_truncation.py:88` | high→low | fix |
| CS-116=135 | `measure_oneshot_poster` الـ`--profile` الافتراضي مش موجود → كراش؛ فشل الرندر بيطبع لا شيء ويخرج 0 | `benchmark/measure_oneshot_poster.py:52` | med→low | fix |
| CS-88 | `measure_subject_judge` "98%" مفيش artifact على الديسك، بس بنداءات مدفوعة | `grounding/measure_subject_judge.py:22` | med→low | verify-first |
| CS-89 | بيوصف مسار `use_rag=False` كأنه "اللي الـLLM بيشوفه" والإنتاج default RAG | `benchmark/measure_evidence_truncation.py:10` | med→low | disclose |

## 📈 TRENDS · MEDIA_PLAN · COMPETITOR

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| CS-44 | SERP seed بلا salience: أول 3 كلمات بالترتيب → "hospital restaurant menu trends 2026" | `trends/sources.py:177` | crit→low | fix ✅ |
| CS-63 | مفيش dedup في بايبلاين الترند — URLs مكررة + Reddit crossposts كل واحد بياكل slot | `trends/engine.py:56` | high→low | fix ✅ |
| CS-107 | `'egypt'` في `_STOPWORDS` → براند مصري مايطابقش سوقه، القطري يقدر | `trends/engine.py:33` | med→low | fix |
| CS-108 | SERP seed بيـhardcode "trends" إنجليزي + سنة 2026 حرفية | `trends/sources.py:178` | med→low | fix |
| CS-64 | `created_ts=None` بيضرب floor الـ48h (بيكاب SERP على 0.8)؛ الكومنت بيقول العكس | `trends/sources.py:203` | med | disclose |
| CS-84 | GeoTargeting مفيهاش country → كل خطة "national" nationwide-of-nowhere (جذر P-51) | `media_plan/schemas.py:158` | med | fix ✅ |
| CS-06 | سطح "online store" resolved=True مسكوك من category label الموديل | `media_plan/builder.py:212` | high | fix |
| CS-20=130 | falsy-`or` بيدهس الـ`None` المتعمّد → national ecommerce بيتحول دايرة 15km | `media_plan/builder.py:403` | med | fix |
| CS-48 | SSRF: `lite_peer_dims` بيتحقق من URL قبل الredirect بس، وبعدين `urlopen` بيتبع الـ302 | `competitor/lite_scrape.py:48` | crit→high | fix |
| CS-57 | `lite_peer_dims` بيرجّع whatsapp/booking False قاسي على أي صفحة → بيفبرك Strength تنافسية | `competitor/lite_scrape.py:68` | high→med | fix ✅ |
| CS-101 | مصفوفة SWOT بتقارن عدّ crawl كامل للsubject vs عدّ homepage للpeers (غير متكافئ) | `competitor/matrix.py:404` | med→low | disclose ✅ |

## 🏛️ GOVERNANCE / DOCS / DEAD-CODE (عنقود 14)

| ID | العنوان | المكان | الحدة | التاج |
|---|---|---|---|---|
| P-97 | facebook_collector بيكشط PII لأفراد مسمّيين عبر Apify — يتوفّق مع PD-10 | `facebook_collector/comments.py:117` | high | decide (D-3) ★cl.14 |
| P-98 | كاب $20/شهر wrapper مش خاصية للـcollector | `facebook_collector/pipeline.py:26` | med | fix ★cl.14 |
| P-99 | كاب اسمه "per post" بيتطبّق per-RUN | `facebook_collector/comments.py:43` | med | fix ★cl.14 |
| P-100=CS-43 | `actor_client` بينزّل الداتاست كامل بلا حد؛ stub ميت مكسور | `actor_client.py:70` | low→med | fix/disclose ★cl.14 |
| P-40=143/156 | كود ميت كأنه حي: render3، `ad_presence`→Threats بيتغذّى من اختبار بس، simulator، Imagen | `competitor/swot.py:286` | low | decide (D-4) ★cl.14 |
| CS-66 | PROMPTS.md بيدّعي "كل برومبت" و≥12 ملف برومبت غايب | `docs/PROMPTS.md:5` | high→med | fix |
| CS-65=86 | anchors في PROMPTS.md بايظة منهجياً (بعد EOF، رمز غلط) | `docs/PROMPTS.md:42,96` | high→low | fix |
| CS-87=109 | PROMPTS.md بيوثّق persona اتحذفت + بيسيب CRAFT_CONTRACT/reel_contracts + بيناقض نفسه | `docs/PROMPTS.md:369` | med/low | fix |

---

## ⛔ REEL — مؤكَّد بس **مؤجّل بأمر المالكة** ("ابعد عن الريل")
P-87 (voiceover مصري [VERIFIED]) · P-88 (`ar-EG-SalmaNeural` متحطوط [VERIFIED]) · P-05/P-89 (escape
اللهجة مكسور) · P-90 (image_select fail-closed للشاشات بس) · P-21/22/23 (بوابات seed/scene/mark fail-open) ·
P-37 ("UNKNOWN BUSINESS" reel) · P-38 (عيوب فريم) · P-07/P-91/P-92 (مسار ميت/كومنت/`x.wav` شارد).

---

## ❌ اتكذّب (مش عيوب — متتشالش من هنا عشان متترفعش تاني)
**REFUTED من المُكذِّب:** P-84 (CS-146/153 — brand-tier بـsource فاضي، مش في الartifacts) · P-86
(CS-102/73 — PASS بيتجاهل lang_mismatch، شفافية مقصودة) · CS-76/77 (منافس floor/CATEGORY_MAP، تصميم) ·
CS-78/139/140/160 (تليفون EG + net.py SSRF — متصلّح/مثبّت أو host ثابت) · CS-95 (0/4 LLM — مُفصَح 3 طبقات) ·
CS-90/114/117/136/115/151 (حساب سكريبتات قياس) · CS-141/142 (fire-and-forget + lock-free reads).
→ ثلاثي الامتثال انهار لـ**P-85 بس**.
**FIXED:** عناقيد 1-7/9/10 · CS-01/03/05 (07-17) · CS-02/04/62 (07-18).
