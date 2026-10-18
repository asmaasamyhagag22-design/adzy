# ADZY — factual copy at the final poster boundary

2026-10-17 · **IMPLEMENTED_LITERAL_BASELINE / SEMANTIC_UNQUALIFIED**. الصرف المدفوع الفعلي **$0**. لا provider حي أو Reel/Veo أو commit/push. هذه نتيجة نطاق محدود، وليست اعتماد جودة دلالية أو production/full-suite certification.

## العيب وإعادة إنتاجه

- المثال القديم `Published delivery process` → `Fast specialist onboarding` يُرفض بالفعل عند `CreativeContractCompiler.compile` بـ`PROOF_MESSAGE_MISMATCH`. الاختبار يحافظ على الرفض؛ لم نضعف compiler أو نغيّر الأدلة لإجازة الوعد. هذا أول حد صحيح مثبت لهذا الزوج، ولا يغطي كل copy لاحقة.
- الهروب المختار من corpus الموجودة هو `avail-h2`: **We never close**. الـ`PosterStrategyVerifier` الموجود يقبله في headline داخل contract متسقة؛ القائمة المحدودة لا تكتشفه. اختبار HTTP/PostgreSQL جديد فشل قبل الإصلاح لأن الناتج وصل `accepted=true` مع Visual PASS مصطنعة. الدليل: `docs/upgrade_audit/evidence/claim_assessment/red.txt`.
- هذا RED يثبت هروب authored copy عند القبول، ولا يثبت أن موديلًا حيًا كتبها أو رسمها. لاحقًا رُسمت العبارة بقالب `poster.template.render_poster_html` الفعلي في المتصفح المحلي، مع منع الأصول الخارجية. PNG المجمدة من هذا الرسم مرّت في canonical HTTP/SQL acceptance مع QA مصطنعة مرتبطة ببايتاتها، وأصبحت **draft**. HTML/DOM/raster و`rendered_path.txt` محفوظة في نفس مجلد الأدلة. لا ادعاء بأن fake QA قاست جودة الصورة.

## التغيير

`poster.claim_assessment` يفصل **proposition candidates بمواقعها** عن **support judgments**. الاستخراج الحالي محافظ على مستوى الحقل الكامل، وليس extractor دلالية ذرية مؤهلة. يغطي business name، headline، subheadline، offerings، CTA، CTA URL، contact، logo text، pricing note، warnings وsocial labels/URLs. الحقول التي قد لا يرسمها layout تظل ضمن الجرد المحافظ.

التقييم typed يربط copy hash، evidence hash، profile/strategy/contract، fields/method/policy، Attempt وSHA-256 للصورة. يحفظ SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE / UNAVAILABLE كأنواع منفصلة؛ الطريقة المفعلة لا تزعم اكتشاف التناقضات دلاليًا، فتُبقيها unresolved. لا نسب ثقة مختلقة. empty/invalid/stale assessment لا تمنح PASS.

المؤهل الآن هو **نسخ statement معتمدة مساوية لاقتباس كامل، لنفس subject ومن مصدر BRAND_OWNED**، مع provenance وقيود معلنة وفحص ledger/numeric/currency الموجود. لا substring matching ولا citation ID كافية. هذا يثبت مطابقة **source assertion**؛ لا يثبت حقيقة مستقلة أو شمولًا دلاليًا. قائمة صغيرة من الدعوات المجردة تستبعد صراحةً من factual denominator؛ default لأي صياغة أخرى هو unresolved.

Canonical compiler يمرر evidence snapshot القائمة. قبل الرسم، repair تستخدم `strategy_repair_concept` القائمة مرة واحدة للـheadline/subheadline، ثم تعيد strategy check وcopy assessment؛ لا تغير contract/product/source. الحقول الجانبية غير المحسومة لا تُرقّى. بعد الرسم تُقرأ brief النهائية ويُعاد التقييم وربطه بالبايتات. أي wording داخل pixels يختلف عن authored brief يظل **NOT_ASSESSED**؛ هذا ظاهر للمستخدم.

Persistence تعيد حساب الأهلية وتقرأ contract السلطوية من Run المقبولة، بدل تصديق caller flags أو evidence يرسلها caller. Visual QA و`qa_passed` تبقيان بُعدًا مستقلًا؛ factual failure يمنع `selected_attempt_id` وaccepted/publishable/deliverable. المسودة تُحفظ في gallery القائمة وoperation state=`draft`، وتُسترجع بالمالك/Run، بينما `last_accepted` تظل مقصورة على المقبول. لا إعادة كتابة للسجلات التاريخية ولا migration جديدة.

داخل نتيجة البوستر توجد شارات منفصلة وتفاصيل claim→quotation→source→verdict، مع تمييز considered evidence عن supporting evidence، وحدود pixels/semantics، وإرشادات remove / evidence-matching rewrite / request evidence or review / abstain. الإصلاح الآلي المنفذ هو scoped copy repair أعلاه؛ الإرشادات ليست workflow جديدة لتجميع الأدلة أو زر موافقة يحوّل unsupported إلى supported. التفاصيل ظاهرة أيضًا في العرض الضيق، وتعود بعد refresh.

Telemetry القائمة تتلقى outcome/version/digests/repair count فقط. لا raw evidence أو prompts أو usage records جديدة. لا semantic/model dispatch جديد؛ تقديرات وحجوزات ومسار الغموض القائم محفوظة.

## إعادة التقييم

```bash
.venv-workflow/bin/python -m grounding.evaluate_poster_claims --canonical \
  --output docs/upgrade_audit/evidence/claim_assessment/evaluation.json
```

الأمر offline: guarded disposable PostgreSQL، actual authenticated review/approval/compiler/HTTP/finalization/gallery، SDK fake transport وfake visual verdicts. لا يقرأ credentials لخدمات مدفوعة. تحتاج Docker وصورة PostgreSQL16 المحلية والبيئة الموجودة.

الـcorpus الأصلية محفوظة: **74** حالة، **66** factual، **8** nonfactual؛ direct **28/28**، held-out **1/38**، false-positive **0/8**. SHA-256: `851cc1177d4666af671bc5e6a642cfe04117aea030d23671e9f842ae79225975`. تقسيمها القديم paraphrases لنفس أنواع propositions، وقياسها extraction فقط؛ ليس اختبار دعم دلالي مستقل.

التقييم الجديد: **20** scenario/evidence distinct، development10 وheld-out10، عربي وإنجليزي، labels **SYNTHETIC_NOT_HUMAN_REVIEWED** كلها؛ human-reviewed **0**. لا paraphrase لنفس free-delivery scenario على الجانبين. لا tuning على held-out. fixtures الحالية تحافظ على بديلين ومنافس بأدلة متسقة حتى تمر canonical readiness؛ عندما رفضتها البوابة أثناء تجهيز fixtures لم تُضعف البوابة.

| القياس offline | development | held-out |
|---|---:|---:|
| Whole-field factual candidate precision | 9/10 | 9/10 |
| Whole-field candidate recall | 9/9 | 9/9 |
| خطأ SUPPORTED بين أحكام SUPPORTED | 0/1 | 0/1 |
| اختلاف verdict عن synthetic factual reference، بما فيه الامتناع بدل contradiction | 3/9 | 3/9 |
| false blocking للصياغات المدعومة | 1/2 | 1/2 |
| abstention/insufficient coverage | 9/10 | 9/10 |

| actual canonical HTTP/PostgreSQL | development | held-out |
|---|---:|---:|
| completed scenarios | 10/10 | 10/10 |
| unsupported claims accepted | **0/7** | **0/7** |
| false blocking supported copy | **1/2** | **1/2** |

آخر runner: **37 passed في59.81s**، بما فيه corpus العشرين وreal-template raster. حزمة العمليات القائمة شغلت منفصلة: **16 passed في79.25s**؛ acceptance/compiler/projection compatibility: **52 passed في2.93s**؛ JavaScript الفعلية: **6 passed**. لا تجمع الأعداد مع reruns المتداخلة. تشغيل جمع ملفي fixtures SQL معًا فشل بسبب DSN مؤقتة موروثة بعد إيقاف container؛ الملفات شغلت منفصلة ولم يُغيّر application admission لإخفاء المشكلة. إعداد corpus ببديل واحد كشف أيضًا مسار `None` قائمًا في positioning proposal؛ احتفظت fixtures ببديلين حسب عقده القائمة، بلا إصلاح للجبهة الأخرى.

**Atomic semantic extraction precision/recall: غير مقاسة، numerator/denominator=null**؛ عدّ كل حقل كمرشح لا يثبت استخراج كل propositions داخله. نتائج corpus عبر HTTP الفعلية، وأعداد الاختبارات الأخيرة، موجودة حرفيًا في `evaluation.json`؛ لا تُخلط مع هذه denominators ولا مع تكرارات التشغيل.

Browser journey: حساب خيالي → التطبيق الفعلي → Create → refresh → فتح Copy evidence. تعرض الواجهة Visual PASS مع copy draft؛ headline غير محسومة وsubheadline مطابقة لاقتباس مع source link. الدليل `browser_refresh.txt` و`browser_draft.jpg` و`browser_persisted.json`. لم تُزر روابط المصادر الخيالية. لا ضمان جودة/provider/OCR من هذا الاختبار.

## التأهيل التالي وحدوده

**لم تُقَس جودة دلالية قائمة على أدلة بواسطة موديل حي.** لا semantic evaluator مفعلة كسلطة شاملة. المطابقات الحرفية ضيقة وقد تحجب copy صحيحة، خصوصًا الترجمات/paraphrases والأسماء/contact بلا اقتباس معتمد في contract. ليست الحزمة fully qualified.

الخطوة التالية المحددة: مراجعة بشرية مستقلة لحدود propositions وsupport labels للحالات العشرين، مع تثبيت scenario split قبل أي live run. بعد ميزانية صريحة فقط: تثبيت exact provider/model ID وextractor/support prompt versions وhashes بجانب policy الحالية؛ shadow evaluation للحالات العشرين: **20 extraction +20 support dispatches**، وحتى **5 repair dispatches** كحد أعلى مخطط = **45**، عبر protected operation boundary الموجودة وتقديرات موجبة وحجوزات قبل كل send. لا سعر دولاري مفترض ولا تشغيل مسموح دون estimates/ميزانية. التقارير تفصل model outputs عن human labels وعن coverage وfalse blocking وaccepted escapes.

التوقف فور unsupported accepted، أو copy/evidence/Attempt mismatch، أو empty extractor PASS، أو تسريب evidence، أو exhausted reservation/unknown provider outcome. لا retries تلقائية بعد ambiguity. إصلاح prompt يتم على development فقط؛ held-out لا يُعاد استخدامه للتطوير ثم يوصف بأنه مستقل. يلزم قرار تأهيل جديد قبل توسيع السلطة الدلالية؛ لا collectors أو distributed workers أو audits إضافية ضمن هذه الحزمة.
