# تجربة قرار الحملة من الرابط

في worktree فرع `codex/adzy-campaign-planner`، استخدم Python باعتماديات التطبيق وChromium وDocker مع صورة `postgres:16-alpine` محلية. يبدأ الديمو بحساب فارغ.

```bash
run_dir=$(mktemp -d)
python -m workflows.public_campaign_demo init --directory "$run_dir/demo"
python -m workflows.public_campaign_demo serve --directory "$run_dir/demo"
```

افتح [التطبيق المحلي](http://127.0.0.1:8020/app) وسجّل ببيانات الديمو المطبوعة. في **Campaign**، أدخل URL والهدف واللغة من [حالات التطوير](cases.json). افتح خطوات البحث، راجع الفرضية والاقتباس، وعدّل الـbrief. احفظه لتجهيز الحزمة، ثم اعرض المقاسين واعتمد النسخة للتصدير. أعد تشغيل `serve` بنفس مجلد الديمو لتستعيد الحملة والموافقة. أوقف الخادم ثم استخدم أمر التنظيف.

```bash
python -m workflows.public_campaign_demo cleanup --directory "$run_dir/demo"
```

## الحزم الناتجة

| الحالة | قرار المسار | النسخة بعد تغيير الاتجاه |
|---|---|---|
| Firefox / قراءة الشرح | [الحزمة المقترحة](results/firefox/recommended.zip) | [الحزمة المعدّلة](results/firefox/campaign.zip) |
| Firefox / التنزيل | [حزمة خيارات التنزيل](results/firefox-download/recommended.zip) | [الحزمة المعدّلة إلى مسار الشرح](results/firefox-download/campaign.zip) |
| LEGO | [الحزمة المقترحة](results/lego/recommended.zip) | [الحزمة المعدّلة](results/lego/campaign.zip) |
| Rijksmuseum | [الحزمة المقترحة](results/rijksmuseum/recommended.zip) | [الحزمة المعدّلة](results/rijksmuseum/campaign.zip) |

## إعادة تشغيل القياسات

```bash
run_dir=$(mktemp -d)
python tools/verify_public_campaign_browser.py --directory "$run_dir/demo" \
  --evidence-dir "$run_dir/evidence" --planner-cases docs/campaigns/planner/cases.json
python -m workflows.verify -q tests/test_campaign_planner.py tests/test_public_campaign_package.py \
  tests/test_adzy_v2_campaign.py tests/test_adzy_v2_research.py \
  tests/test_route_persistence_classification_2gb.py tests/test_security_headers.py \
  tests/test_ssrf_coverage.py tests/test_source_export.py
```

لمقارنة Qwen يلزم Ollama على loopback ونسخة `qwen3.5:9b` محمّلة. التجربة تسجّل كل طلب والنتيجة الخام والمهلة. لا يحتاج التطبيق نفسه Ollama.

```bash
python tools/compare_campaign_planner.py --output "$run_dir/direct" "$run_dir"/evidence/{firefox,firefox-download,lego,rijksmuseum}/draft.json
python tools/compare_campaign_planner.py --output "$run_dir/replay" docs/campaigns/planner/results/direct/*.json
```

أرشيف الطلب المباشر يحفظ `source_blocks` و`request_sha256` اللذين يحددان ما أُرسل في التشغيل الأصلي. تتطابق الصفحات الأصلية مع حالات التطوير، وفي حالتي Firefox اختلفت ترتيب ومحتويات المقتطفات عن لقطة الجولة اللاحقة. قارن لقطات التشغيل من مسار واحد إذا كنت تريد قياسًا منضبطًا بنفس المقاطع. [البروتوكول](PROTOCOL.md) · [سجل التنفيذ](EXECUTION.md) · [تقرير المقارنة](RESULTS.md) · [معمارية Campaign](../transformation/ARCHITECTURE.md)
