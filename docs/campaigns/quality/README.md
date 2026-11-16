# تشغيل ومراجعة حملة ADZY ثابتة

فرع التسليم `codex/adzy-campaign-quality` يبدأ من `85e1edfba1bf698c09eff5998ca8785633be98bb` على `codex/adzy-campaign-planner`. ابدأ بـ[سجل التنفيذ](EXECUTION.md) ثم [النتائج](RESULTS.md).

## شغّلها من الواجهة

استخدم بيئة Python للتطبيق، Chromium من Playwright، وDocker مع `postgres:16-alpine`. في الـworktree المستقلة:

```bash
run_dir=$(mktemp -d)
python -m workflows.public_campaign_demo init --directory "$run_dir/demo"
python -m workflows.public_campaign_demo serve --directory "$run_dir/demo"
```

افتح [التطبيق المحلي](http://127.0.0.1:8020/app) وادخل بحساب الديمو المطبوع. من **Campaign** انسخ رابط وهدف ولغة [أي حالة تطوير](cases.json) أو [إحدى حالتي التقييم](heldout.json). راجع خطوات البحث والاقتباس والفرضية. احفظ الـbrief واعرض PNG بالمقاسين. بعد اعتماد النسخة نزّل ZIP. غيّر الاتجاه وتأكد إن زر التنزيل اختفى لحد ما تعتمد النسخة الجديدة. أعد تشغيل `serve` بنفس الدليل لاستعادة آخر نسخة معتمدة. بعد انتهاء المراجعة أوقف الخادم ونظّف بيئة الديمو المعزولة.

```bash
python -m workflows.public_campaign_demo cleanup --directory "$run_dir/demo"
```

## إعادة القياس

```bash
run_dir=$(mktemp -d)
python tools/verify_public_campaign_browser.py --directory "$run_dir/demo" \
  --evidence-dir "$run_dir/evidence" --planner-cases docs/campaigns/quality/cases.json
python tools/verify_public_campaign_browser.py --directory "$run_dir/heldout" \
  --evidence-dir "$run_dir/heldout-evidence" --planner-cases docs/campaigns/quality/heldout.json

python -m pytest -q tests/test_campaign_copy_quality.py tests/test_campaign_planner.py \
  tests/test_public_campaign_package.py tests/test_adzy_v2_campaign.py tests/test_adzy_v2_research.py \
  tests/test_route_persistence_classification_2gb.py tests/test_security_headers.py \
  tests/test_ssrf_coverage.py tests/test_ssrf_sinks.py tests/test_lite_scrape_ssrf.py tests/test_source_export.py
```

إعادة جولة `85e1edf` من worktree مفصولة تحتاج المدخلات فقط. احذف `copy_edit` و`rejected_headline` و`copy_receipt` من JSON الديمو لتشغيل الإصدار السابق. البحث الحي قابل لتغيّر الصفحة أو فشل الوصول. استخدم ZIP وسجل الأدلة لإعادة مقارنة النص على نفس المدخلات.

الجولتان المدفوعتان تقرآن الحزم السابقة وتولّدان نصًا فقط. سجل [ledger](ledger) يحتوي المدخلات والـprompt hash والردود الخام والتكلفة المقدّرة. اطلع عليه قبل أي إعادة. الأمر أدناه يوضح تشغيلًا جديدًا عند وجود اعتماد صالح ومساحة في ميزانية المهمة، باستخدام سجل الصرف المستمر. لا يحتاج التطبيق ولا العرض إلى الاعتماد.

```bash
python -m tools.experiment_campaign_copy --provider vertex --round direct \
  --ledger docs/campaigns/quality/ledger --key-file /path/to/approved-provider.env \
  docs/campaigns/quality/results/{firefox,firefox-download,lego,rijksmuseum}/before.zip
```

استخدم `--round compact` لشرط الكتابة من الـbrief. لتطابق أسماء الحالات في سجل التكرار الحالي مرّر `before.zip` من كل مجلد حالة. سجل التجربة يرفض إرسال نفس اسم الجولة والحالة والمزود مرتين. المقارنة آلية تحريرية وتبقى قابلة للمراجعة من [الأحكام لكل اقتراح](results/editorial.json).

## مراجعة اقتراحات الموديل من محرر الحملة

افتح «تعديل نص الاتجاه»، ونزّل المسودة. أداة الكتابة تأخذ الـJSON وتطابق اقتراحاتها مع الـbrief والأدلة والوجهة. بعد التجربة ارفع `suggestions.json` إلى محرر نفس المسودة. افتح كل زاوية واقرأ الاقتباس المعروض، واختر «جرّب الحقل» للنص المناسب. حفظ النموذج يطبّق بوابة الدليل في الخادم. بعد الحفظ يلزم اعتماد النسخة الجديدة.

```bash
python -m tools.experiment_campaign_copy --provider vertex --round compact \
  --ledger docs/campaigns/quality/ledger --key-file /path/to/approved-provider.env \
  --packet-output /tmp/suggestions.json /path/to/campaign-draft.json
```

لإعادة تشغيل اقتراح Rijksmuseum المحفوظ بدون نداء أو اعتماد:

```bash
python -m tools.experiment_campaign_copy --packet-output /tmp/rijksmuseum-suggestions.json \
  --from-receipt docs/campaigns/quality/ledger/compact-rijksmuseum-vertex.receipt.json \
  docs/campaigns/quality/results/rijksmuseum/draft.json
python -m tools.summarize_campaign_quality
```

ملف الاقتراحات مربوط بالمسودة المحددة التي اشتُق منها. عند بدء مسودة حية جديدة بنفس المدخلات، استخدم JSON الذي نزل من الواجهة في أمر إعادة بناء الملف. ينجح ذلك إذا تطابقت اللقطة والـbrief والوجهة مع سجل المحاولة الأصلية.

عند إرسال مسودة JSON جديدة يستخدم السجل `run_id` لمنع إعادة إرسال نفس المسودة إلى نفس المزوّد والجولة بالخطأ. تغيير نسخة المسودة يعطي معرفًا جديدًا.
