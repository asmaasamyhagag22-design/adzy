# ADZY — حد توليد البوستر القابل للاسترجاع

2026-10-17 · النطاق: canonical `POST /api/generate` المستخدم في واجهة ADZY، خارج عقد graph القابلة للإعادة. المصدر: التنفيذ الحالي، لا إعادة تدقيق للمنتج. الصرف الفعلي `$0`؛ لا نداء مدفوع حي، ولا تغيير Reel، ولا commit/push.

## خريطة النداءات المدفوعة

`adzyMakeAd → /api/generate → canonical compiler/admission → _emit → generate_image → _render_real → generate_poster → acceptance → existing gallery`.

| النداء الفعلي | التكلفة/الحساب الموجود قبل الحزمة | الإعادة الموجودة | حماية هذه الحزمة |
|---|---|---|---|
| DNA، concept/copy، sense-check، semantic grounding، design/prompt، product measurement، OCR بالـLLM، vision QA، caption → `GeminiCaller → models.generate_content` | `Usage` ترجع tokens/thoughts وتقدير float؛ بعض المستهلكين يهملونها. ليست حجزًا ذريًا | `max_retries + 1`؛ default=2 محاولتين، ومنها إعادة بسبب parsing | كل إرسال داخل العملية يسجل `usage_records` مرتبطًا بنفس Run/Attempt؛ أسماء مجموعات LLM محفوظة في `stage` |
| الخلفية الفعلية الافتراضية، أو البوستر oneshot → `generate_oneshot_poster → models.generate_content` | `vertex_spend.guard_image/record` وعدادات transport؛ لا owner operation identity | أربع محاولات لكل model، delays 0/20/45/90، model fallback؛ الخلفية تحاول مرتين؛ QA قد تعيد المرشح | حجز قبل كل إرسال، حتى الفشل/fallback؛ SDK attempts=1، لا إعادة بعد ambiguity |
| classic fallback helpers/injected legacy cascade → `VertexImagenProvider.generate → models.generate_images` | صورة في ملف، لا الحجز الجديد سابقًا | Ultra→fallback؛ style→outpaint→T2I في cascade غير الافتراضية | guard موصول بنفس السياق؛ ليست الادعاء بأن cascade الافتراضية تستخدمه |
| style/outpaint → `ImagenEditProvider → models.edit_image`؛ upscale → `models.upscale_image` | نداءات إضافية يمكن أن تكلف | fallback/best-effort في المستدعي | guard موصول قبل النداء؛ ambiguity تتجاوز broad `except Exception` |
| البحث الخارجي الاختياري → search provider ثم research LLM | قد تكون له فاتورة أخرى | provider-dependent | **معطل داخل العملية الموثوقة**؛ تستخدم الأدلة المراجعة. ليس مزوّد بحث محميًا |

كل SDK call يحمل Run واحدًا وAttempt القائم؛ معرف UsageRecord لكل إرسال، model/stage، usage الخام، provider response/request reference إن توفر. أسماء الموديلات تُقرأ من التنفيذ/config؛ لا أسعار أو بلد hardcoded جديدة. بعض مراحل الجدول اختيارية أو تتوقف بفشل مبكر؛ ليس كل طلب ينفذها كلها.

**غير مشمول:** CLI، `/poster/from-profile`، multipart upload/reference، calendar، content-only، وأي استعمال للمساعدات خارج `paid_scope`. لا ادعاء حماية كل مداخل البوستر. مسار JSON المدفوع يرفض غياب canonical strategy/key؛ previews القديمة بلا paid flag تبقى متوافقة.

## الهوية والحجز والاسترجاع

- الهجرة `0006_poster_operations` توسع `runs` و`usage_records` الموجودين؛ لا جدول عمليات أو ledger منافس. approvals وRuns/Attempts والقبول والمعرض وreview graph تستمر كمصادر الحقيقة الحالية. وصول DB من التطبيق يظل عبر `api/persistence.py`.
- المفتاح owner-scoped وفريد؛ SHA-256 على JSON الطلب validated/default-expanded، باستثناء المفتاح، مع ترتيب المفاتيح. نفس المفتاح/الطلب يعيد العملية حتى بعد تغير approval؛ اختلاف المحتوى409. عملية جديدة فقط بهوية جديدة وبعد compiler يعيد فحص approval/current strategy/profile تحت brand lock.
- قفل owner row يسلسل حجز كل البراندات لهذا المالك. الحد: `Σ(reserved_usd + settled_usd) + new_reservation <= owner_cap` لجميع العمليات المشمولة، بلا reset زمني ضمن هذه الحزمة. داخل العملية: مجموع تقديرات **كل الإرسالات** لا يتجاوز reservation. كل المقارنات Decimal وNUMERIC(20,9).
- `reserved` ظرف قبول؛ `estimated` مجموع تقديرات الإرسال؛ raw usage/tokens و`known_usage_usd` منفصلان؛ `settled_usd` فاتورة نهائية مسجلة. لا مقارنة float. usage/cost غير المتوفرين NULL، حتى مع رد HTTP رفض؛ الصفر مثبت تلقائيًا فقط إذا لم يحدث إرسال أصلًا. نجاح الصورة لا يحرر الحجز مع تكلفة غير معلومة.
- هذا **حد قبول بالتقديرات، وليس سقف فاتورة المزوّد**. لا bounding موثق لتوكنات/سعر invoice؛ الفاتورة قد تتجاوز الحجز. لا صرف حي ولا pricing certification هنا.
- يلزم operator config: `ADZY_POSTER_BUDGET_USD`، `ADZY_POSTER_RESERVATION_USD`، و`ADZY_POSTER_CALL_ESTIMATES_JSON` كـmap من `method:model` أو `method/group:model` إلى مبلغ موجب string. الـmethod العام estimate صريح لكل مجموعة تستخدم نفس الموديل؛ يمكن تخصيص المجموعة. لا default سعر صفري. الأرقام0.6/1.0 في الاختبارات **مصطنعة**.
- نفس المنفذ يحتفظ بقفل PostgreSQL session advisory دون transaction مفتوحة أثناء provider I/O؛ العملية تُحفظ قبل الإرسال. GET/duplicate لا يعيدان التوليد. هذه حزمة synchronous محلية، بلا worker/distributed scheduling أو external exactly-once guarantee؛ تحتاج اتصال PostgreSQL مباشرًا/session-stable، وليس transaction pooling.
- قبل أي dispatch: known local failure. بعد durable send intent دون receipt: unknown، حتى لو حدث الانقطاع قبل الشبكة فعليًا. timeout/5xx/cancellation/crash لا تثبت توقف المزوّد أو فاتورته. explicit 4xx rejection known generation failure، لكن التكلفة لا تُفترض صفرًا. unknown يحتفظ بالحجز ولا يصل لأي retry/fallback.
- APIs المستخدمة synchronous generateContent/Imagen؛ لا request-status/result retrieval API موصول لهذه الطلبات، ولا provider idempotency guarantee مفترضة. response/request references للمتابعة وليست handle مزيفة لـLRO. الاسترجاع الآلي يبحث في **المعرض القائم بالمالك/Run مباشرة**؛ publication مكتملة قبل ضياع HTTP تستعيد accepted.
- التسوية اليدوية للمشغّل فقط: `get_persistence().poster_operations().reconcile_receipt(actor, key, receipt_reference=..., final_costs={usage_record_id: decimal_string, ...}, terminal_confirmed=True)` بعد التحقق الخارجي من **إيصال نهائي لكل إرسال**. ليست owner HTTP endpoint، ولا تحقق توقيع إيصال آلي. رفض الإلغاء/المهلة المحلية كدليل؛ التسوية تسجل actual حتى لو أعلى من الحجز، وتفرج reservation، ولا ترسل للمزوّد. بلا صورة مقبولة محفوظة تصبح local result failed، مع التكلفة الصادقة. exact receipt replay لا يكرر التسوية.
- UI يحفظ المفتاح والطلب قبل dispatch في localStorage حسب المالك والبراند. retry يحتفظ بالطلب الأصلي؛ Make another بعد response loss يفحص القديم أولًا. refresh يعرض latest admitted/running/accepted/failed/unknown ومعه last accepted مستقل. unknown يعرض مرجع المتابعة ويتطلب reconciliation؛ failed يسمح variation صريحة. تعطل localStorage يمنع الإرسال.

## الإثبات المرصود

الاختبارات الجديدة: `tests/test_poster_operation_boundary.py`، `tests/poster_boundary_support.py`، `tests/poster_operations_frontend.test.cjs`.

- آخر rerun بعد منع إعادة فتح حالة terminal بواسطة recovery متأخرة: **16/16 passed في68.85s**، كلها اختبارات HTTP/PostgreSQL الجديدة، ومنها4 subprocess crash points؛ `final_operations.txt`. لا يُضاف هذا العدد للحزمة المتداخلة التالية.
- حزمة الحدود/schema/gallery: **117 passed / 24 skipped، 71.14s**؛ الـskips =23 gallery SQL-gated بلا test DSN عام +1 historical single-revision schema parity، وليست بديلًا عن اختبارات PostgreSQL الجديدة الفعلية. توافق graph/poster/acceptance: **91 passed،29.07s**. JS: **9 passed** (5 جديدة +4 exact SWOT موجودة). المخرجات الخام في `docs/upgrade_audit/evidence/poster_boundary/`؛ التشغيلات المتداخلة لا تُجمع.
- mutation runtime دون تعديل السورس: تعطيل guard داخل عملية اختبار منفصلة جعل503 يرسل **20** نداء fake وفشل الاختبار (1 expected failure). مع الحارس أعاد نفس الاختبار **إرسالًا واحدًا** ونجح (1 passed،14.11s). هذا قياس فرق السلوك؛ ليس ادعاء RED تاريخي على working tree قبل الحزمة.
- PostgreSQL16 disposable في container مملوك للاختبار، namespace/ack guard، شبكة خارجية محظورة. HTTP حقيقي للتطبيق مع auth حالي وreview→exact approval؛ Google SDK حقيقي بـhttpx fake transports، لا استدعاء مزوّد حي.
- duplicate concurrent: عملية محلية واحدة وقبول fake واحد؛ نفس المفتاح/محتوى مختلف409. حجزان0.6 ضد1.0 **لبراندين مختلفين بنفس المالك**: قبول واحد. كشف/كتابة المالك الآخر404 دون إرسال.
- subprocess hard exit في أربع نقاط: قبل dispatch، بعد intent وقبل send، بعد external acceptance وقبل receipt، وبعد gallery commit وقبل HTTP. الاسترجاع لا يغير Run ID ولا يعيد قبولًا خارجيًا.
- SDK مضبوط على خمس محاولات في fake client؛503 لا يفلت لحلقة SDK، و429 كل محاولة داخليًا مسجلة ومقيدة بالحجز. مراحل image+LLM متعددة، غياب التسعير، actual pipeline ambiguity، receipt settlement فوق التقدير، current approval gate، head/model schema parity، وHTTP refresh بعد reset persistence.
- اختبارات JS تشغّل ملف ADZY الحقيقي في DOM مصطنعة، بما فيها fresh module مع نفس localStorage؛ ليست browser/mobile certification. مسار النجاح يستخدم synthetic render/QA seam مع acceptance/gallery الحقيقيين؛ اختبار ambiguity يدخل `generate_poster` الفعلية. Imagen/edit/upscale guard wiring/config types مفحوصة محليًا؛ ليست live أو fake-transport runtime certification لهذه الثلاثة.

الحدود المتبقية: لا قياس live image/QA quality أو فاتورة نهائية أو مزود reconciliation آلي؛ لا crash recovery لملف generated diagnostic وحده قبل gallery publication (يظل unknown ولا يُرسل ثانية). atomicity بين existing creative finalization وgallery publication لم تُعد بناؤها؛ window غير المكتملة تتطلب مراجعة محلية. لا full-suite أو production authentication/deployment certification. لوحات/براندات/صور الإنتاج لم تُستخدم. أثناء تطوير fixtures رفض existing `uq_assets_storage_key` finalization لبايتات صورة متطابقة عبر براندين؛ لم يُعاد بناء gallery/asset reuse لمعالجة هذا القيد هنا. pre-existing work محفوظ، والتغييرات في working tree فقط.
