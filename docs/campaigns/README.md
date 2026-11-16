# حملات ADZY للمراجعة المحلية

كل حزمة ZIP فيها بوستر طولي 1080×1350، ومربّع 1080×1080، وملف `campaign.json` فيه الـbrief والبدائل والاتجاهات والنص والمخطط النصي والدليل وقياسات الرسم. مجلد `initial` يحتفظ بالاتجاه السابق للمقارنة. `brief.json` هو مدخل إعادة إنشاء الحزمة من التطبيق.

| البراند | الفكرة | المخرجات |
|---|---|---|
| Buffalo Burger | شيدر كلاسيك ولا هالبينو؟ | [التشغيل والحزمة](buffalo/README.md) · [المقارنة](buffalo/design/comparison.png) |
| Firefox | ماذا يحظر متصفّحك؟ | [الحزمة](firefox/deliverables/campaign.zip) · [الطولي](firefox/deliverables/poster-portrait.png) · [السابق](firefox/deliverables/initial/campaign.zip) |
| LEGO Pick a Brick | ما فكرتك للقطعة الأولى؟ | [الحزمة](lego/deliverables/campaign.zip) · [الطولي](lego/deliverables/poster-portrait.png) · [السابق](lego/deliverables/initial/campaign.zip) |
| Rijksmuseum | See two artworks side by side | [الحزمة](rijksmuseum/deliverables/campaign.zip) · [الطولي](rijksmuseum/deliverables/poster-portrait.png) · [السابق](rijksmuseum/deliverables/initial/campaign.zip) |

المقارنة والمراجعات في [سجل التنفيذ](EXECUTION.md) و[تقرير المراجعة](REVIEW.md). قرارات التشغيل في [معمارية Campaign](transformation/ARCHITECTURE.md).

## إعادة تشغيل الديمو

من جذر worktree التكامل، استخدم Python باعتماديات التطبيق وChromium الخاص بـPlaywright وDocker مع صورة `postgres:16-alpine` موجودة محليًا. إعداد Python في [دليل التشغيل](../getting-started.md). يتصل السيرفر العام بمصادر الدليل المحددة، ويستعمل ملفاته وقاعدة بياناته المحلية للمراجعة.

```bash
run_dir=$(mktemp -d)
python -m workflows.public_campaign_demo init --directory "$run_dir/demo" \
  --case docs/campaigns/firefox --case docs/campaigns/lego --case docs/campaigns/rijksmuseum
python -m workflows.public_campaign_demo serve --directory "$run_dir/demo"
```

افتح [واجهة التطبيق المحلية](http://127.0.0.1:8020/app)، وسجّل بالبيانات التي طبعها `init`. اختر براندًا، ثم **Campaign → حملة من مصادر عامة**، وارفع ملف `brief.json` من مجلد البراند. افتح المعاينتين، ثم اعتمد النسخة للتصدير المحلي. اختر اتجاهًا آخر وأعد المراجعة. أوقف `serve` وأعده بنفس المجلد لاستعادة النسخة والموافقة وملفات PNG. لتنظيف الديمو أوقف الخادم، ثم نفذ الأمر التالي.

```bash
python -m workflows.public_campaign_demo cleanup --directory "$run_dir/demo"
```

لتشغيل الفحص الآلي بنفس المسار الطبيعي وكتابة ملفات النتائج إلى مجلد جديد:

```bash
run_dir=$(mktemp -d)
python tools/verify_public_campaign_browser.py --directory "$run_dir/demo" --evidence-dir "$run_dir/evidence" \
  docs/campaigns/firefox docs/campaigns/lego docs/campaigns/rijksmuseum
python -m workflows.verify -q tests/test_public_campaign_package.py tests/test_adzy_v2_campaign.py \
  tests/test_adzy_v2_research.py tests/test_ssrf_coverage.py \
  tests/test_route_persistence_classification_2gb.py tests/test_security_headers.py
```

يوفر الديمو سجلات تعريف محلية للبراندات بأسمائها وروابطها. البحث عن نص الأدلة يحصل فعلًا من الواجهة. النسخة تعتمد على ملف Creative brief مؤلف وقابل للمراجعة، ويختبر الفحص تبديل الاتجاه وحفظه. الملف المصدر متاح لإعادة التحرير قبل رفع نسخة جديدة. لاستعادة حملة محفوظة احتفظ بقاعدة البيانات ومجلد `BASEERA_WEBAPP_DIR` معًا. قد ينتج إعادة إنشاء الحملة من موقع حي مخرجات مختلفة عند تغير الصفحة أو وقت الالتقاط.
