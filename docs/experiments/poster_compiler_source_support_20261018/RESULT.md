# إصلاح دعم الرسالة وتسليم الحالة العربية — 18 أكتوبر2026

## POSTER-COMPILER-20261018 — APPROVAL_PRESERVED / STOPPED_DISPATCH_LIMIT_EXHAUSTED

موافقة المالكة مسجلة بالفعل؛ لا قرار موافقة جديد مطلوب. أُصلح compiler واستُعيد نفس LangGraph workflow `dcd515ad-12dd-4fc6-bf37-bdce4bcea834` إلى READY_FOR_CREATIVE، ثم نُفذت الرحلة المصرح بها بنفس `portfolio-firefox-ar-20261018-v1`. انتهت failed/POSTER_DISPATCH_LIMIT_EXHAUSTED عند **12/12**؛ كل النداءات received، رفض provider=0، unknown=0. صُورتان حقيقيتان رفضتهما Visual QA؛ لا نتيجة canonical، delivery=404/POSTER_NOT_FOUND، ولا final download/checksum. لا إرسال آخر أو عملية بديلة.

السبب: «الخصوصية مُدمجة» ليست عنوان اتجاه فقط؛ هي consumer_value وSOURCE_ASSERTION لها اقتباس مستقل حرفي. حظر «معظم المتعقّبات» assertion آخر. ربط compiler السابق بينهما بcontainment/token overlap كان يسأل عن التشابه بدل دعم كل وعد. العقد@2 يثبت كل assertion باقتباسها وProfileSnapshot/source_ref/page_url/subject، ويحفظ IDs/القيود/المادة المعتمدة، مع proof_entailment=NOT_ASSESSED؛ لا ادعاء entailment أو تحقق مستقل من صحة قول البراند. لا NONFACTUAL blanket أو synonyms/threshold/Firefox exception أو semantic authority.

نفس موافقتي PostgreSQL IDs/timestamps، ونفس plan SHA-256 `19e47012…45aca` وstrategy output `0f7948e7…59ea1`؛ لم تُكتب hashes جديدة للمادة أو التفويض. graph recovery لا يعيد approval، ويتحقق من subject/currentness/hash. matching الادعاءات حرفي محافظ؛ inference/paraphrase غير مثبتة تفشل وتحتاج مادة معتمدة مدعومة أو سياسة دلالية مؤهلة؛ لا قبولها بالتشابه.

التكلفة: حجز$2 قائم؛ estimates للنداءات **$0.436428800**؛ estimate headroom **$1.563571200** داخل الحجز، ليس مبلغًا مفكوكًا أو فاتورة. known/settled/invoice=null، المتبقي من الإرسالات0. لم تُحرر حجوزات. الخادم عاد no-spend على8010. semantic SHADOW_UNQUALIFIED؛ لا Reel/semantic experiments/external telemetry/commit/push/deploy.

التحقق المركز: compiler20 + adversarial18 + graph19 + HTTP7 + helper2 + operations16 + delivery12 = **94 passed** في الملفات/العمليات المحددة. لا full suite. تشغيل graph+HTTP ثم operations+delivery معًا أظهر fixture DB lifetime errors بعد مرور17 و16 على الترتيب؛ أُعيدت الملفات المتأثرة في عمليات مستقلة ونجحت، بلا تخفيف gates. fixture compiler القديمة كانت metadata-only؛ أضيفت لها quotes/profile anchors اصطناعية صريحة، وليست أدلة حية مصطنعة.

مراجعة AI للصورتين: النص العربي والعنوان وقيد «معظم» ظاهرة؛ سطر الدليل صغير، CTA فيه FIREFOX باللاتينية، وتصوير نمط حياة staged/stock. Visual QA رفضت كلتيهما؛ الأولى contrast3.97:1 والثانية7.14:1 من السجل. لا factual assessment نهائي محفوظ بعد توقف العملية، ولا pixel certification؛ دعم authored assertions في العقد لا يعني factual PASS للصورة. صور التشخيص في outputs/posters/poster_784e5cf4.png وposter_013bf7a1.png؛ ليست accepted assets.

الأدلة: `docs/experiments/poster_compiler_source_support_20261018/RESULT.md` +context.json؛ وحالة التشغيل المحلية `.adzy-portfolio/state/live-result.json`/live-operation.json/review-result.json/current-accounting.json/delivery-diagnostic.json. snapshots قبل الإصلاح محفوظة باسم *before-compiler-repair.json. السجلات السابقة التالية تاريخية ولا تمثل انتظار موافقة الآن. حزمة المصدر القديمة في /tmp تسبق هذا الإصلاح ولم يُدّع تحديثها أو نشرها.

---

### الاختبارات وسبب التغيير

- الكود المتغير: poster/strategy_contract.py، workflows/runtime.py، workflows/portfolio_live.py.
- اختبارات المخرج: tests/test_compiler_source_support.py؛ tests/test_gate2c_canonical_poster.py؛ recovery في tests/test_review_workflow.py؛ عدم تكرار الموافقات في tests/test_portfolio_authorization.py.
- الاختبارات تثبت أن rollback للـcompiler القديم يرفض الحالة العربية (الفشل الأصلي محفوظ بـPROOF_MESSAGE_MISMATCH)، وأن fast specialist onboarding لا يُستمد من published delivery process، وأن overlap/approval/citation وحدها لا تمنح support. تغيير quantifier/entity/negation/condition أو quote/profile/subject/type/contradiction/IDs يفشل. تغيير metadata في subject لا يحدث approval hash تلقائيًا.
- mapping يحفظ consumer_value كما اعتُمد؛ لا يعيد تفسيره كعنوان NONFACTUAL ولا يستبدله بـreason. مصدر الدعم المباشر معلن، وليس claim أن reason يثبت value دلاليًا.
- لا qualifying semantic policy ولا human label أو benchmark جديد.

### الصور الحقيقية، مرفوضة للتسليم النهائي

- `outputs/posters/poster_784e5cf4.png`: SHA-256 `a1addf6ea2dba29b36fdf3b425f336f23a908a81ef998bc742fa27ccf644ec1b`؛ 4364216 bytes. تشخيص محلي فقط؛ لا download checksum match.
- `outputs/posters/poster_013bf7a1.png`: SHA-256 `173791d3f992fd960014135cc62b0a64ae6e559c5c4e795fa230f0dda1eb9702`؛ 4119630 bytes. تشخيص محلي فقط؛ لا download checksum match.

### حدود النسخة

أصبحت المطابقة حرفية محافظة؛ أي paraphrase/inference لا تحمل anchor حرفيًا لن تُقبل بالـoverlap القديم. هذه كلفة fail-closed مقصودة؛ دعم صياغات أوسع يحتاج تأهيلًا دلاليًا مستقلًا، وليس synonym patch. بوابة exact download لم تُتجاوز، ولا نعد فشل provider limit نتيجة factual/visual ناجحة.
