# كل الأوامر

كل أمر تحت **كامل** — انسخيه والصقيه زي ما هو. كلهم بيفترضوا إنك في:
`C:/Users/Admin/Desktop/dev/scraper_v01`

**البايثون:** `C:/Users/Admin/.conda/envs/marketing_scraper/python.exe`
(الـ`python` العادي في الـPATH **مش** فيه مكتبات المشروع — لازم المسار الكامل ده.)

---

## ١. الداشبورد

**تشغيل:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m uvicorn api.main:app --port 8000 --reload
```

بعدين: `http://localhost:8000/app` · والصفحة التعريفية على `http://localhost:8000/`

* `--reload` بيخلي السيرفر يعيد التحميل لوحده لما الكود يتغيّر. **من غيره لازم توقفيه وتشغليه تاني بعد أي تعديل** — ده كان بيضيّع وقت كتير.
* **مهم:** لازم تشغّليه من `scraper_v01` نفسها. لو اشتغل من فولدر تاني هيرجّع `404` على `/app`.
* لو البورت مشغول: غيّري `--port 8000` لـ`--port 8001` وافتحي `http://localhost:8001/app`.

**إيقاف:** `Ctrl+C` في نفس التيرمينال.

**مين شغال على البورت (لو رفض يشتغل):**

```bash
netstat -ano | findstr :8000
```

---

## ٢. الفحوصات — شغّليهم لما أي حاجة تبوظ

**كل راوت في الداشبورد** (السيرفر لازم يكون شغال):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe info/check_dashboard.py
```

**على براند معيّن:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe info/check_dashboard.py --brand alfouadpharmacies_com
```

**كل زرار موصول بكود موجود:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe info/check_wiring.py
```

**اللوج بتاعهم:** `logs/dashboard_check.log` (بيتراكم، متاريخ — تقدري ترجعيله بعد ما حاجة تقع).

**كل التستات** (~١٤ دقيقة):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m pytest -q tests/
```

**تستات جزء واحد بس** (أسرع كتير):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m pytest -q tests/ -k "reel"
```

بدّلي `reel` بـ`poster` أو `swot` أو `competitor` أو `social`.

---

## ٣. سكرايب براند جديد

**من الداشبورد:** الصقي اللينك في خانة الـURL على الصفحة الرئيسية.

**من التيرمينال:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m business_profile https://example.com --out outputs/example_profile.json
```

---

## ٤. الريل

**⚠️ محتاج جوجل شغالة — واقف دلوقتي بسبب الفوترة.**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m reel outputs/alfouad_profile.json --out outputs/reels/alfouad.mp4 --language ar
```

**الفلاجات المهمة:**

| الفلاج | يعني إيه |
|---|---|
| `--language ar` / `en` | لغة الكلام والكابشن (من غيره بيتقرر من أدلة البراند) |
| `--max-seconds 15` | طول الفيلم. **١٥ هي الافتراضي** = ٣ لقطات مولّدة + لقطة منتج بنرندرها |
| `--no-video` | **من غير أي صرف** — بيطلع ريل بتدرّجات لونية. للتجربة السريعة |
| `--real` | من غير AI — بيحرّك صور الموقع الحقيقية |
| `--scale 0.5` | نص الدقة، أسرع بكتير للمعاينة |
| `--frames 3` | عدد لقطات أقل (بيكسب لو أقل من المشتق) |

**تجربة من غير أي فلوس:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m reel outputs/alfouad_profile.json --out outputs/reels/test.mp4 --no-video --scale 0.5
```

---

## ٥. البوستر

**⚠️ محتاج جوجل شغالة.** من الداشبورد: Studio ← اختاري البراند ← Generate.

---

## ٦. السوشيال ميديا

**الأرقام اللي متسحوبة خلاص** (مجاني، من الملفات المحفوظة):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -c "import json,pathlib,sys; sys.path.insert(0,'.'); from scripts.pull_instagram_apify import signals_from_apify_items; from social_intel.performance import top_performers, creative_lessons; from social_intel.snapshot import load_ig_snapshot; [print(p.stem, top_performers(signals_from_apify_items(load_ig_snapshot(p)[0])).own) for p in pathlib.Path('runs/social_snapshots').glob('*_ig.json')]"
```

**في الداشبورد:** Studio ← SWOT ← لوحة **«What gets the likes»**.

**سحب جديد من إنستجرام** (بيصرف على Apify):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe scripts/pull_instagram_apify.py --username <الحساب>
```

الملفات بتتحفظ في `runs/social_snapshots/<اسم_الموقع>_ig.json`.

---

## ٧. المنافسين

**إضافة منافس بإيدك** (ده اللي بيملّي الضعف والتهديدات في الـSWOT):
الداشبورد ← Studio ← SWOT ← لوحة **Competitors** ← اكتبي الاسم والموقع ← Add.

**شوفي المسجّلين لبراند:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -c "import sys; sys.path.insert(0,'.'); from competitor.registry import list_competitors; [print(c['name'],'|',c.get('website'),'|',c.get('source')) for c in list_competitors('https://alfouadpharmacies.com/en')]"
```

ملفات الرجيستري في `registry/`. اسم الملف مشتق من موقع البراند.

**شوفي الـSWOT هيتغيّر إزاي قبل ما تخزّنيه** (صفر صرف، وماتكتبش حاجة):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m info.swot_diff
```

لبراند واحد اكتبي جزء من اسمه: `python -m info.swot_diff alfouad`
وبـ`--offline` مابيقراش مواقع المنافسين خالص (أسرع، بس الفرق بيبان أقل من الحقيقة).

**تنفيذ التغيير على السجلات** (بيكتب — خدي نسخة احتياطية الأول):

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m info.swot_apply
```

من غير `--write` دي **بروفة** بتطبع الخطة ومابتكتبش. ضيفي `--write` لما الخطة تعجبك.
دي **دمج مش استبدال**: الفلاتر بتشيل، لكن السطر اللي مدخله ناقص (Places موقوف، أقران أو trends
السجل ما احتفظش بيهم) **بيتحافظ عليه**. والأداة **بترفض الكتابة** لو أي قايمة كانت مليانة هتبقى
فاضية.

النسخة الاحتياطية بتاعة 2026-08-02 في `outputs/webapp/brands_backup_2026-08-02_pre_swot_apply` —
للرجوع انسخي ملفاتها فوق `outputs/webapp/brands` (ملف ملف، مش حذف الفولدر — السيرفر ماسكه).

الأداة بتطبع لكل براند سطر **inputs** فيه عدد الأقران والـtrends والـinsights المتاحة. لو براند
اتخزّن من غير أقران، سطور المقارنة **مش هترجع** — ودي مش مشكلة كود. والسطور اللي بعلامة `~`
معناها **مفتاح Places موقوف**، مش إن حاجة اتشالت.

---

## ٨. الفلوس

**كل الصرف لحد دلوقتي:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -c "import sys; sys.path.insert(0,'.'); import vertex_spend as v; print('جوجل الشهر ده: \$%.2f' % v.month_spend_usd())"
```

**السجلات:** `runs/vertex_spend.jsonl` (جوجل) · `runs/elevenlabs_spend.jsonl` (الصوت) · `runs/apify_spend.jsonl` (إنستجرام).

**السقوف** في `.env`:

| المتغير | بيعمل إيه |
|---|---|
| `VEO_MAX_USD_PER_RUN` | أقصى صرف فيديو للرندر الواحد (دلوقتي `1000`) |
| `VERTEX_MONTHLY_CAP_USD` | سقف شهري. **مش متحط** — يعني مفيش سقف |
| `BASEERA_ENABLE_PAID_IMAGES` | لازم `1` عشان الصور المدفوعة تشتغل |

---

## ٩. لما حاجة تبوظ

| الأعراض | السبب | الحل |
|---|---|---|
| `/app` بيرجّع 404 | السيرفر اشتغل من فولدر غلط | شغّليه من `scraper_v01` |
| زرار مابيعملش حاجة | الكود اتغيّر والسيرفر ماعادش شغّل | أعيدي التشغيل، أو استخدمي `--reload` |
| `403 dunning` | الفوترة | Google Cloud Billing |
| `401` وبيطلّعك برة | التوكن خلص | سجّلي دخول تاني |
| الـSWOT ضعفه فاضي | مفيش منافسين | ضيفي منافس من لوحة Competitors |
| «no snapshot collected» | البراند ده ملوش سحب سوشيال | `scripts/pull_instagram_apify.py` |
| الريل طلع صور ثابتة | Veo فشل ووقع على Ken Burns | شوفي اللوج — غالباً الفوترة |

**دايماً ابدأي بـ:**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe info/check_dashboard.py
```
