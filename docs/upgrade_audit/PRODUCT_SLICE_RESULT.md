# Product slice / WP1 — COMPLETE_SCOPED (2026-10-16)

المستخدم الجديد أكمل داخل **Adzy shell الفعلية**: signup → URL جديد → SWOT ومصادرها → موافقة النسخة المعروضة → objective/strategy → Proof/Watch-outs → موافقة StrategyVersion → CreativeContract → poster → نتيجة QA معلنة كاختبار synthetic. هذا **offline integration** مع PostgreSQL16.15 وخدمات المنتج الحقيقية؛ ليس شهادة live quality أو production readiness.

| الحالة | النتيجة وحدودها |
|---|---|
| IMPLEMENTATION_STATUS | مكتمل للنطاق. WP0 محفوظ؛ لا schema/orchestration/provider rewrite. |
| OFFLINE_TEST_STATUS | targeted31 passed؛ narrow final rerun5 passed **متداخلة**؛ Node12 passed (8 Adzy جديدة +4 WP0). لا full-suite claim. |
| POSTGRES_INTEGRATION_STATUS | PostgreSQL16.15 حقيقية، existing Alembic head، guarded disposable DB. HTTP7 passed؛ persisted exact approvals/accepted Attempt/failed Runs/owner isolation. ليست fake concurrency؛ لم نعد اختبارات WP0 concurrency أو ندّع exactly-once. |
| BROWSER_BEHAVIOR_STATUS | observed assembled shell، actual auth/routes/services/storage، desktop1440/mobile390، refresh + process restart، stale/rejection/insufficiency/provider/QA-unavailable. External providers فقط synthetic. |

## 1. الحكم في PROOF_MESSAGE_MISMATCH

**الرفض صحيح؛ توقع نجاح creative compile في الحالة الأصلية غير صحيح.**

- `tests/test_gate2b_positioning_contracts.py::_strong_brand` يورد consumer value **Fast specialist onboarding** وRTB **Published delivery process**. الاقتباس يثبت وجود process ولا يثبت السرعة أو ارتباطها بالـonboarding.
- `strategy/positioning/service.py:243::_propose` قد يستخدم fallback إلى RTB موجودة إذا لم يجد overlap. كفاية البحث/موافقة الاستراتيجية لا تساوي creative readiness؛ لا نغيّر هذه الدلالة ضمن WP1.
- `poster/strategy_contract.py:237::_semantic_supports` و`:474::CreativeContractCompiler.compile` يرفضان proof غير الداعمة. لم يتغير verifier أو allowlist أو claims الأصلية.
- أعيد الاختبار الأصلي على نسخة baseline قبل التغييرات: **1 failed** بنفس الاستثناء (`semantic_red.txt`). تعديل ROOT داخل النسخة المؤقتة كان فقط لتوجيه Alembic إلى المشروع الحقيقي؛ أعيدت نسخة baseline إلى fingerprint الأصلية بعد reproduction.
- الحالة الأصلية بقيت negative SQL test عند `tests/test_gate2b_postgres_strategy.py:337`. success test عند`:203` يستخدم **سيناريو fictional آخر** من `tests/product_slice_fixtures.py`: guided specialist onboarding واقتباس published guided specialist onboarding process. لا ندعي أن هذه الأدلة تصلح الوعد القديم بالسرعة. success/negative كلاهما نجحا **2 passed** (`semantic_green.txt`).

## 2. التغييرات المحددة

| الموضع | السلوك الناتج |
|---|---|
| `frontend/index.html:1628,1642` + `frontend/assets/adzy-app.js:539::renderResearch` | SWOT review + citations/readiness/empty sections؛ next action؛ Generate/Regenerate/Approve/Review strategy داخل Think الحالية. لا إظهار شامل للـlegacy. |
| `adzy-app.js::adzyApproveSwot,renderStrategy,decideStrategy` | frozen domain/Run/epoch مرتبطة بالمحتوى المعروض؛409 يعيد تحميل current ويطلب مراجعة جديدة بلا POST تلقائية. |
| `adzy-app.js::selectBrand,bridgeLegacyAuth` | مسح snapshots/contract عند تغيير البراند؛ تجاهل responses المتأخرة؛ logout يبطل responses المعلقة ولا يضيف creative لhistory جلسة جديدة. |
| `adzy-app.js::renderApproval,runAnalysis,pollAnalysisJob` | prerequisites/explicit objective/approval/rejection/gaps/regeneration؛ loading وبوابات no-approval عند insufficiency/rejection. |
| `adzy-app.js::renderCreative,adzyMakeAd` | compile من approved StrategyVersion؛ إزالة fallback إلى contract تاريخية بعد compile error. يرسل UUID الموافق عليها، ويترك derived CTA للسيرفر بدلاً من إرسالها كuser override. |
| `api/webapp_images.py::CanonicalPosterQARejected` + `api/routes/generate.py::_emit` | نفس رفض QA ونفس failed Run، مع typed safe projection لـQA_FAIL/QA_UNVERIFIED/retry. لا صورة ولا gallery write عند الرفض، ولا raw provider exception للعميل. |
| `adzy-app.js::qaState,renderPoster` | IDs لا تعني Claims checked؛ factual verification unavailable/unknown. synthetic vision pass معلنة test evidence؛ failure banner يميز النتيجة القديمة المعروضة عن المحاولة الفاشلة الجديدة. explicit retry فقط. |
| `frontend/assets/adzy.css` + Work key handler | wrapping/reachable controls/focus-visible؛ Enter/Space لفتح Work card. query versions للـJS/CSS تمنع hard-cached assets القديمة. |

اكتُشف CTA conflict بالمتصفح: contract اشتقت Visit website رغم عدم وجود CTA source؛ إعادة إرسال default كـpreference تجعل compiler تطالب بدليل override. إصلاح الواجهة يرسل preference فارغة ويعيد السيرفر نفس الاشتقاق، **دون إضافة CTA مصطنعة أو توسيع القائمة المسموحة**.

واكتُشف أن rejected candidate قد تبقى `eligible_for_approval=true` من حيث evidence sufficiency؛ الواجهة أصبحت تعرض rejection وتطلب generation جديدة، ولا تعيد وصفها ready/locked. حقول approval الفعلية هي المرجع.

## 3. نتائج الرحلة والأدلة

| التجربة | ما ثبت |
|---|---|
| fresh signup/URL | real signup/JWT/require_user؛ actual scrape route/job/recorder/profile commit؛ collection fictional فقط. لا dependency override للمستخدم أو store. |
| SWOT review/approve | sources/readiness ظاهرة؛ Run `3dca5c72-0d4e-4a15-8010-c02c38020edb` approved بالضبط. refresh أعاد نفس الموضوع/الحالة. |
| strategy review/approve | objective صريحة؛ Proof/Watch-outs راجعناهما؛ StrategyVersion `1e6ad573-d027-429d-8fb8-d47c442732b3` approved بالضبط. refresh ثم process restart احتفظا بها. |
| poster success | actual compiler/pipeline/concept constraints/HTML compositor/vision policy/attempt hash/acceptance/PG finalization/gallery. selected Attempt `697887da-780d-4f07-9ea0-84ec95e9998b` succeeded/qa_passed=true؛ exact strategy UUID محفوظ في gallery. |
| result refresh/restart | الصورة من managed persisted history؛ restart لم يعِد generation: model/image counters=0 قبل النقرة التالية. `restart_result_proof.json`؛ لم يُختبر قتل العملية أثناء external acceptance. |
| stale SWOT | عرض revision A، توليد B عبر route أخرى، click على A→409/zero approvals؛ B ظهرت مع request review. نقرة صريحة ثانية فقط وافقت B. `stale_swot_{before,after}_click.json`. |
| stale strategy | عرض candidate A ثم generate B عبر route؛ click Lock A→409/zero strategy approvals؛ لا auto-decision لـB. `stale_strategy_{before,after}_click.json`. |
| rejected strategy | real reject محفوظ؛ refresh يُظهر rejected؛ Create button disabled/no contract. `rejected_strategy_proof.json`. |
| insufficient | حذف trust/audience في fictional collection؛ real sufficiency→CANNOT_PROCEED/5 verifier issues/no Lock. action recovery وروابط المراجعة؛ `insufficient_candidate.json`. |
| provider error | actual /generate→502؛ failed Run/Attempt بلا selected artifact؛ رسالة retry واضحة؛ `provider_error.png`. |
| QA failure/unverified | actual acceptance رفضت output؛ HTTP502 معQA_FAIL/QA_UNVERIFIED، accepted=false؛ history لم تتغير. browser أظهر unavailable وغيرdeliverable مع تصريح أن الصورة الظاهرة سابقة. |
| ownership | مستخدم synthetic ثانٍ: read/intel/SWOT approve/strategy decide/contract/generate→404 مثل absent؛ history فارغة؛ no mutation/no image provider calls. HTTP test يغطي كل هذه الحدود. |
| navigation | GETs وcontract compilation read-only لا model/image calls؛ no generation إلا نقرة explicit. |
| desktop/mobile/keyboard |1440 و390؛ poster/research scrollWidth390 على mobile؛ desktop result1441 على1440 وinsufficiency391 على390 (1px غير حاجب، وليس zero-overflow claim). primary actions داخل العرض؛ Tab نقل focus إلى Reject معsolid outline؛ Enter فتح Work card. ليس accessibility audit شاملًا. |

لقطات مفيدة في `docs/upgrade_audit/evidence/product_slice/`:
- `research_desktop.png`, `research_mobile.png`, `stale_swot_mobile.png`.
- `poster_final_desktop.png`, `poster_desktop.png`, `poster_mobile.png`, `provider_error.png`, `qa_unverified_mobile.png`, `insufficient_mobile.png`.
- `accepted_poster.png` هي bytes الناتج fictional المقبول، و`accepted_artifact_summary.json` تحفظ lineage/actual QA decision دون image payload أو credentials.
- CUA fullPage screenshots فشلت تقنيًا مرتين؛ استخدمت viewport screenshots. ذلك لا يعني app failure.

`browser_console.json`: أخطاء API409/502 المتعمدة فقط في النافذة المرصودة؛ لا uncaught frontend exception مرصودة. ليس إثباتًا لغياب كل console error في كل جلسة. `browser_observations.json` يسجل measurements/IDs؛ proof files تحفظ sanitized method/path/status، ولا Authorization/request bodies.

## 4. ما تم استبداله بـfakes، وما بقي فعليًا

`docs/upgrade_audit/diagnostics/product_slice/server.py` يستورد **api.main:app** مع lifespan. قبل import يعطّل .env loader، يمسح credential env، يمنع Python non-loopback sockets، يوجه artifacts إلى temp. existing startup cleanup/reconciliation تلمس test DB فقط. PostgreSQL app DB منفصلة عن pytest DB، وكلاهما fresh/guarded/disposable؛ existing migrations فقط.

حدود الاستبدال:
1. external collector `scrape.run_pipeline_job`: profile fictional؛ real request/auth/job/commit تبقى.
2. external competitor discovery/SWOT synthesis: dataclass responses fictional؛ real serialization/content hash/immutable Run/currentness/finalization تبقى. trend collectors أعادت no items تحت network guard؛ لا live market research.
3. `business_profile.llm.default_caller`: typed concept/vision responses؛ DNA/design/caption غير المجهزة ترفض ويعمل deterministic fallback الموجود. لا stub لcompiler/verifier/QA acceptance.
4. external image transport `poster.oneshot.generate_oneshot_poster`: PIL abstract background أو injected provider failure. real HTML composer وChromium وposter pipeline يبقون.
5. QA response **model boundary فقط**: `poster_vision_qa` الحقيقي يبني policy/check completion/hash والقبول الحقيقي يقرر. أثناء تجهيز fake، `has_latin_text=true` رُفض حتى مع overall_pass=true؛ fixture صححت إلىfalse وفق prompt الإنجليزي الحقيقي. لا QA gate bypass.
6. DNS fictional `.example.test` فقط له synthetic public address؛ real SSRF validator باقٍ. Chromium يمنع http(s) assets؛ app CSP تمنع remote fonts/scripts/connects. showcase temp empty حتى لا تُحمّل customer assets.

لا external authenticated action ولا paid spend؛ **model/media $0**. anonymous official Docker image + official Playwright Chromium download فقط. لم تُغيّر requirements/lockfiles أو تثبّت stack المشروع. runtime موجودة أصلًا؛ Chromium headless shell كان missing مثبتًا، فنُزل تحت `/tmp/adzy-product-browser-runtime` لتشغيل compositor الحقيقي. هذا ليس شرط تثبيت جديد للمنتج.

## 5. الأوامر والنتائج الفعلية

من repository root، باستخدام `/tmp/adzy-upgrade-audit-venv/bin/python` (`PY` أدناه) وNode الموجودة (`NODE`):

```bash
PY=/tmp/adzy-upgrade-audit-venv/bin/python
NODE=/home/asmaa/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
# Guarded disposable settings أُنشئت محليًا؛ لا تستعمل ملف DSN قديم بعد cleanup.
WP0_WITH_POSTGRES=1 WP0_DATABASE_CONFIG=/tmp/adzy-product-postgres.json "$PY" docs/upgrade_audit/diagnostics/wp0/run_tests.py /tmp/adzy-product-slice-baseline/tests/test_gate2b_postgres_strategy.py::test_new_approved_strategy_compiles_contract_without_creating_a_poster -q
# semantic_red.txt: 1 failed in5.77s (expected reproduction).
WP0_WITH_POSTGRES=1 WP0_DATABASE_CONFIG=/tmp/adzy-product-postgres.json "$PY" docs/upgrade_audit/diagnostics/wp0/run_tests.py tests/test_gate2b_postgres_strategy.py::test_new_approved_strategy_compiles_contract_without_creating_a_poster tests/test_gate2b_postgres_strategy.py::test_unrelated_original_process_does_not_prove_fast_onboarding -q
# semantic_green.txt: 2 passed in8.25s.
WP0_WITH_POSTGRES=1 WP0_DATABASE_CONFIG=/tmp/adzy-product-postgres.json "$PY" docs/upgrade_audit/diagnostics/wp0/run_tests.py tests/test_gate2b_postgres_strategy.py tests/test_gate2c_canonical_poster.py tests/test_poster_acceptance_red_boundary.py tests/test_product_slice_projection.py -q
# targeted_tests.txt: 31 passed in23.62s; one Starlette deprecation warning.
PRODUCT_SLICE_HTTP=1 "$PY" docs/upgrade_audit/diagnostics/wp0/run_tests.py tests/test_product_slice_http.py -q
# http_tests.txt: 7 passed in37.95s against running actual synthetic app.
"$NODE" --test tests/product_slice_frontend.test.cjs tests/wp0_swot_frontend.test.cjs
# frontend_tests.txt: 12 passed /0failed (8 new Adzy +4 existing WP0).
WP0_WITH_POSTGRES=1 WP0_DATABASE_CONFIG=/tmp/adzy-product-postgres.json "$PY" docs/upgrade_audit/diagnostics/wp0/run_tests.py tests/test_product_slice_projection.py tests/test_gate2c_canonical_poster.py::test_canonical_provider_failure_is_typed_and_closes_the_real_run tests/test_poster_acceptance_red_boundary.py -q
# final_projection_tests.txt: 5 passed in2.32s; overlaps the31, not 5 new distinct tests.
PLAYWRIGHT_BROWSERS_PATH=/tmp/adzy-product-browser-runtime "$PY" -m playwright install --only-shell chromium
# chromium_setup.txt: official download because executable was absent.
"$PY" docs/upgrade_audit/diagnostics/product_slice/server.py
# server.txt: actual ASGI app; initial missing relative Alembic script_location fixed in diagnostic only.
PRODUCT_SLICE_RESUME=1 "$PY" docs/upgrade_audit/diagnostics/product_slice/server.py
# same owned guarded database/artifact directory across process restart.
```

Node tests run actual Adzy module/handlers with startup disabled, synthetic DOM/API: **unit coverage**, not browser integration. They complement the CUA actual shell; do not substitute it. Intermediate fake/DOM/setup failures were corrected at diagnostic seams (missing geometry/DOM querySelector, missing geography, QA field semantics, cached JS). Passing mocks do not certify provider truth or factual precision.

## 6. Scoped diff، الحفظ، والحدود المتبقية

`evidence/product_slice/scoped.patch` تقارن **working tree عند بدء WP1**، وليست git HEAD القديم. `scoped_stat.json` يفصل التطبيق/tests/diagnostics/docs؛ `preservation.json` يقارن fingerprints الأصلية وgit index/status؛ `final_status.txt` يحفظ الشجرة الكاملة. Pre-existing changes بما فيها WP0 محفوظة؛ لم تُعد هذه التغييرات ضمن إنجاز WP1. ملفات production المعدلة5 فقط: frontend index/JS/CSS، generate route، webapp_images؛ مع existing test واحد هو test_gate2b_postgres_strategy. PROGRESS فقط من docs القديمة؛ الباقي new files في هذه الحزمة. لا commit/push/stage/schema/dependency/Reel/Veo edits.

حدود صريحة:
- synthetic provider/vision لا تقيس live creative quality، semantic claim coverage، sales/ROI، الأسعار أو Google account readiness.
- لا full suite، ولا Reel tests، ولا multiworker crash-window/paid reconciliation أو migration-stack compatibility certification.
- process restart تم بين العمليات، لا أثناء provider acceptance. job handles الخاصة بالتحليلات لا تزال session polling؛ task-resume UI بعد فقدان handle ليست مكتملة. Historical trace binding بعد تبديل StrategyVersion لم تُثبت في هذه الجولة.
- failed Runs محفوظة بالـDB، لكن **لا failed-run inspector في shell بعد refresh**؛ history تعرض الصور المقبولة السابقة. error/retry banner للمحاولة الأخيرة session UI؛ لا ندّعي persistency لهذا banner أو وجود delivery subsystem.
- هذه الجولة لم تثبت أن كل supported brand/locale يمكنه النجاح؛ fixtures لغةen/سوقQatar فقط، ولا hardcoded geography في production.
- لا owner input لازم لإغلاق هذه الحزمة. cleanup لحاوية/خادم هذه الجولة في `cleanup.json`؛ إعادة التشغيل تحتاج fresh guarded DB، لا reuse لملفات /tmp القديمة.

**المعلم التالي الواحد:** claim truthfulness عند المخرج؛ ابدأ بـF01 في FINDINGS.md وبـheld-out escape الموجودة، ثم اختبر actual canonical path وtyped verdict/abstention. إصلاح شارة F07 هنا عرض صادق فقط، لا حل semantics. بعد ذلك paid-operation safety، ثم LangGraph، بالترتيب الذي حددته المالكة.
