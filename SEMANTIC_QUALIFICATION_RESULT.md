# تحديث حي — 2026-10-17: smoke3 توقفت بعد رفض أول إرسال

**BLOCKED_PROVIDER_REJECTED / SEMANTIC_UNQUALIFIED**. تفويض المالكة الصريح $1.11/حد6 مسجل. نفذ الـrunner الموجود smoke3: generateContent1 extraction مسجل rejected، support0؛ إضافة metadata GET1 ناجح لنفس الموديل/global (إجمالي طلبات خدمة الموديل2). لا resolved inference version أو token usage أو outputs دلالية؛ HTTP status التفصيلي غير محفوظ، فلا يُجزم بأنه403.

estimated commitment$0.183500800؛ حجز قائم$0.367001600؛ known/settled/invoice=null؛ unknown provider outcome0، billing unknown. زمن runner9.511s. الحالتان الباقيتان لم تُرسلا. لا retries/fallback أو فك حجز أو مالك آخر. human labels=null، no qualification/activation. التفصيل والأدلة: [SMOKE3_RESULT](docs/experiments/copy_semantic_v1/live/SMOKE3_RESULT.md) و`live/smoke3.summary.json`؛ `comparison.json` محدثة. هذه **AI analysis وليست human labels**.

ما يلي سجل الإعداد السابق للتجربة؛ أرقام0 calls/$0 وغياب التفويض أدناه تاريخية وقد تجاوزها التحديث أعلاه.

---

# ADZY — semantic copy qualification v1

2026-10-17 · **القرار: BLOCKED**. التنفيذ shadow جاهز للاختبار المصرح، لكنه **SEMANTIC_UNQUALIFIED** ولم يُفعّل كسلطة قبول.

1. **ما شُغّل على موديل حقيقي:** لا شيء؛ **0** extraction/support/provider calls، وصرف مدفوع **$0**. لا توجد ميزانية مالكة ملموسة في التكليف أو التفويض السابق.
2. **ما كان mocked:** ردود atomic extraction/support، وSDK HTTP transports وcredentials وهمية؛ PostgreSQL والخدمات والحجوزات والحساب والـSDK فعليون محليًا. الردود المصطنعة اختبرت الحراسة، وليست دليل دقة دلالية.
3. **ما راجعه بشر:** **0/20** حالات، و0 outputs. الـpacket بلا تنبؤات أو إجابات synthetic، وجميع خانات المراجعة البشرية `null`.
4. **هل تحسن قبول المدعوم بالموديل؟** **غير مقاس**؛ 0 حالات مرشح حقيقية. ظل الـcohort الحرفي عند1/2 لكل split. التحسن الحتمي المنفصل أدناه لا يُحسب تحسنًا بالموديل.
5. **هل زاد قبول غير المدعوم؟** **غير مقاس للمرشح**، لا يُسجّل صفرًا مضللًا على denominator فارغ. الـbaseline الحالي0/7 لكل split. في اختبارات الحراسة، حتى fake judge تؤيد وعدًا خاطئًا لا تختار صورة ولا تغيّر القبول.
6. **ما ينقص القرار:** تفويض مالي صريح، إثبات إتاحة exact model على المشروع الحالي، ثم مراجعة بشرية مستقلة للـatomic/support labels قبل qualification. لا LIMITED_PILOT_CANDIDATE الآن، ولا تفعيل آلي.

## الفحص والتغيير المحدود

- `grounding.subject_judge` الموجود يفحص same-subject للرقم/الكلمة المشتركة، وبرومبته تقول قبول المشكوك فيه؛ ليس proof entailment. `strategy_contract._semantic_supports` deterministic token/substr relation داخل compiler، لا extractor أو judge دلالية عامة. احتُفظ بهما لنطاقهما، وأُعيد استخدام EvidenceLedger وnumeric/currency validators وGeminiCaller والـpaid-operation boundary. المرحلتان الجديدتان داخل `poster.claim_assessment`، والتجربة امتداد للمقيّم الموجود `grounding.evaluate_poster_claims`؛ لا evaluator منافسة أو إطار agents جديد.
- التشخيص في cohort: `Delivery is free`→`Delivery costs you nothing` و`الصيانة تشمل قطع الغيار`→`قطع الغيار مشمولة في الصيانة` إعادة صياغة مدعومة بحسب labels **المصطنعة**، تحجبهما المطابقة الحرفية. ليستا دليلًا على نقص citation أو قياس ترجمة بين لغتين. dropped conditions/conditional discounts، wrong product، numeric/currency mismatch، غياب الدليل وعدم كفايته تظل فئات مختلفة.
- **إصلاح حتمي مثبت:** compiler كان يسقط اقتباسات name/contact الموجودة في ProfileSnapshot، ثم يطالب business_name/contact_line بـmarketing-claim IDs. أضيفت مراجع field-specific مقتبسة كاملة من snapshot؛ المطابقة الدقيقة تُسمّى `IDENTITY_CONTACT_METADATA`، ولا تعفي أي promise في تلك الحقول أو في headline. missing provenance واسم/contact مضاف لهما وعد يظلان محجوبين. policy الحرفية الحالية@1.1؛ الأصل@1 محفوظ.
- قياس نفس fixture الخيالية قبل/بعد، خارج cohort20: **metadata matched0/2→2/2؛ authored-copy eligible=false→true**. إعادة تشغيل الكود الأصلي المجمد تثبت الفرق؛ لا model call ولا independent-truth claim أو complete-poster measurement. RED/green ودليل replay في `docs/upgrade_audit/evidence/semantic_qualification/` و`comparison.json`.

## Shadow والحراسة

Stage A ترجع atomic propositions مع field/span/subject/fact/qualifiers وcoverage spans؛ Stage B تحكم المجموعة في إرسال واحد مع verdict، citation ID/quotation وشرح قصير. تُفحص spans بما فيها حدود Unicode، وتغطية أحرف كل الحقول، ومجموعة IDs، وصحة الاقتباسات، وentity/scope declarations والقيود الرقمية/العملة. لا تتحول extraction أو explanation إلى evidence. schema-valid لا تعني semantic-correct.

الفارغ/المفقود/المبتور/invalid/timeout أو تجاوز حدود input/propositions لا يعطي PASS، ولا يُقصّ الإدخال لإكمال عدد النداءات. تصريح all-nonfactual يبقى **امتناعًا غير مؤهل** بلا support dispatch. حتى coverage الكاملة المعلنة من الموديل تسمى `MODEL_DECLARED_COVERAGE_UNQUALIFIED`؛ لا تثبت عدم تفويت proposition.

النتيجة تربط copy/evidence/profile/strategy/contract، مصدر Attempt/bytes إن وُجد، dispatch Run، requested/resolved model versions وprompt hashes. `finish_shadow` يحفظها منفصلة في output للعملية المخصصة، مع no media/no selection؛ لا gallery write أو تعديل سجل قبول تاريخي. عمليات shadow لا تستبدل latest poster في الواجهة. ambiguity تحتفظ بالحجز ولا تُعاد. كل send يستخدم existing UsageRecords/reservations/owner lock؛ كل cohort يستخدم مالكًا خياليًا واحدًا لحجز إجمالي فعلي. لا paid graph nodes أو إعادة توليد صور/إصلاح مدفوع.

## التجربة والأرقام

الأصل74 حالة محفوظ حرفيًا؛ cohort20 وتوزيع scenario/evidence10development/10held-out ونتائج baseline مجمدة مع hashes. الـheld-out هنا split موروثة، synthetic answers سبق كشفها عند قياس literal؛ ليست عينة بشرية جديدة مستقلة. لم تُشاهد semantic model predictions لها ولم يُضبط prompt عليها. المسودات السابقة للتسجيل محفوظة، والـmanifest النهائي يثبت الكود والـschemas والـSDK والـprompts قبل أي تشغيل حي.

| القياس لكل split | frozen literal | corrected literal | real candidate |
|---|---:|---:|---:|
| قبول المدعوم | 1/2 | 1/2 | غير مقاس؛ denominator0 |
| قبول غير المدعوم | 0/7 | 0/7 | غير مقاس؛ denominator0 |
| الامتناع | 9/10 | 9/10 | غير مقاس؛ denominator0 |
| تسمية التناقض بشكل مختلف عن المرجع synthetic | 2/2 | 2/2 | غير مقاس |

اختلاف التناقض في literal هو امتناع عن الدعم، لا قبول للادعاء. **Atomic precision/recall=null**: لا atomic reference labels بشرية حاليًا. complete-poster/pixel-text eligibility غير مثبتة؛ cohort headline فقط. زمن literal المحلي لكل حالة محفوظ في comparison؛ **model latency/usage/known billed cost غير مقاسة**. fake usage لا تدخل هذه المقارنة. الـrunner يدعم مقارنة captures حية لاحقًا بالـbaseline على نفس الإدخال، ويفصل hypothetical would-support عن القبول الفعلي.

Focused regression: **79 passed /2.19s**؛ آخر exact Vertex/global SDK +disposable PostgreSQL: **5 passed /9.20s**؛ existing poster duplicate/acceptance regression منفصل **1 passed /5.83s**. تشمل missing estimate، exhausted reservation، SDK503 مرة واحدة، MAX_TOKENS بلا support call، owner isolation، persisted refresh، ولا media selection رغم fake false support. لا full-suite أو live/provider certification، ولا جمع للـreruns المتداخلة.

## الحزمة والخطوة التالية

- `docs/experiments/copy_semantic_v1/human_review_packet.json`: الملف الوحيد المرسل للمراجع؛ source/context/copy، إرشادات وأماكن boundaries/labels بلا إجابات. dataset والـreview-ID map والنتائج منفصلة عنه. لا نملا labels بشرية بالنيابة عن المراجع.
- `docs/experiments/copy_semantic_v1/experiment_manifest.v1.json`: hashes، source/runtime/schema/prompt versions، dispatch inventory، limits، smoke/stop conditions وcriteria مسجلة قبل model results. توجد نتيجة المقارنة في `comparison.json`.

```bash
.venv-workflow/bin/python -m grounding.evaluate_poster_claims \
  --semantic-report docs/experiments/copy_semantic_v1
```

هذا الأمر يعيد القياس المحلي بلا provider أو DB. الـlive runner يتطلب `--semantic-live … --authorization … --batch smoke3`؛ لا يوجد authorization ملف ممول ضمن الحزمة. لا يعيد العملية بعد unknown أو result موجودة؛ subsequent batches تتطلب مراجعة تشغيلية صريحة للدفعة السابقة.

المرشح المحدد **`gemini-3.5-flash-lite`، Vertex/Agent Platform، Standard global** موثق GA؛ إتاحته على حساب المشروع **UNVERIFIED**. السعر الموثق في17أكتوبر: input **$0.30/M** وresponse+reasoning **$2.50/M**؛ صلاحية هذا التحقق حتى24أكتوبر؛ يلزم تحقق جديد بعده وفق manifest. [الموديل](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite)، [دورة الحياة](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)، [السعر الرسمي](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing).

الحدود:32KiB للنص+system+schema،20propositions،120coverage spans،8citations/judgment؛ output8192 للاستخراج و4096 للدعم، thinking MINIMAL، timeout60s، SDK/application attempts1، tools/search/repair0. allowance تقديرية محافظة65536input+65536response/reasoning لكل إرسال: **$0.1835008/dispatch**. smoke3≤6dispatches وحجز **$1.1010048**؛ إجمالي≤40 وحجوزات **$7.340032**. **$7.35 اقتراح تفويض وليس تفويضًا قائمًا أو سقف فاتورة معتمدًا**؛ usage/settlement المجهولة لا تصبح صفرًا.

الخطوة المحددة: سلّمي الـpacket لمراجع مستقل، ثم تفويض الميزانية/المشروع وإثبات model access حديثين، ثم smoke3. criteria المسجلة: المدعوم2/2 لكل split، غير المدعوم0/7، امتناع≤3/10، صفر observed atomic extraction errors على الحالات ذات المرجع البشري، وضوابط runtime صادقة. هي شروط pilot صغيرة وليست population guarantee؛ الموديل لا يُفعّل تلقائيًا حتى لو اجتازها.

لا Reel/schema migration/dependency upgrade/commit/push أو README claims جديدة؛ الشغل السابق محفوظ.


## إيضاح مُلحق — 2026-10-17: تشخيص smoke3

لا يمكن استعادة HTTP status أو exception class الأصليين. rejected محفوظة كنتيجة للمصنّف القديم، لا كإيصال HTTP مستعاد؛ لا نجزم سبب الرفض. أُصلح فقد التشخيص محليًا بلا طلبات جديدة، مع إبقاء outputs وmanifest الأصلية كما هي والحجز$0.367001600 قائمًا، known/settled=null. المصدر المعدّل لا يعيد تشغيل manifest القديمة. التفاصيل: SEMANTIC_SMOKE_DIAGNOSTICS_RESULT.md وlive/smoke3.diagnostic_clarification.json.


## 2026-10-17 — successor diagnostic1 (محاولة جديدة مقصودة)

رفض extraction الجديدة مثبت هذه المرة: HTTP400 / INVALID_ARGUMENT / ClientError؛ safe message=invalid argument، الحقل المخالف غير محفوظ بالتنقيح. نداء جديد1، تراكمي3/6، support0، resolved version/usage=null. نفس المالك؛ الحجز القديم$0.367001600 محفوظ والجديد مثله، الإجمالي$0.734003200 ضمن$1.11؛ known/settled/invoice=null. لا تخمين للسبب التفصيلي ولا إعادة إرسال/qualification/activation. manifest/outputs/auth الأصلية كما هي؛ successor والتقرير: docs/experiments/copy_semantic_v1_diagnostic1_20261017/DIAGNOSTIC1_RESULT.md.


## 2026-10-17 — diagnostic2 (no qualification)

ناتج JSON حقيقي بعد provider-only schema projection، requested/resolved gemini-3.5-flash-lite؛ strict validation رفض INVALID_COPY_SPAN29>28، ولم تُرسل support. السبب التاريخي المحدد400 غير مثبت. طلب جديد1؛ تراكمي4/6؛ الحجوزات$1.101004800 من$1.11، known/settled/invoice=null. labels لم تتغير والمرشحة غير مفعلة. تقرير محدود: docs/experiments/copy_semantic_v1_diagnostic2_20261017/RESULT.md.
