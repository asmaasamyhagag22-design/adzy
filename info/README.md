# ابدأي من هنا

الفولدر ده هو كل اللي تحتاجيه عشان تشغّلي المشروع وتفهمي حالته من غير ما ترجعي لأي شات.

| الملف | فيه إيه |
|---|---|
| **[RUNBOOK.md](RUNBOOK.md)** | **كل الأوامر** — تشغيل الداشبورد، سكرايب، SWOT، بوستر، ريل، سوشيال، فحوصات |
| **[DASHBOARD.md](DASHBOARD.md)** | الداشبورد شاشة بشاشة: كل زرار بيعمل إيه وبينادي إيه |
| **[STATE.md](STATE.md)** | حالة المشروع النهاردة — إيه شغال، إيه واقف، وإيه **الحاجز الوحيد** |
| **[SESSION_2026-08-01.md](SESSION_2026-08-01.md)** | كل حاجة اتعملت في الجلسة دي بالأرقام المقيسة |
| `check_dashboard.py` | بيمشي على كل راوت ويقول شغال ولا لأ |
| `check_wiring.py` | بيتأكد إن كل زرار موصول بكود موجود |
| `check_vertex.py` | **ابدأي بيه لو حاجة مدفوعة وقعت** — بيطبع التطابق التلاثي وبيعمل نداء حقيقي بأقل من سنت |
| `check_features.py` | **بيسأل السؤال التالت: الفيتشر بيطلّع حاجة حقيقية؟** — ١٣ فيتشر على براند مخزَّن، بصفر صرف |

---

## أسرع ٣ أوامر

**١) شغّلي الداشبورد**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe -m uvicorn api.main:app --port 8000 --reload
```

بعدين افتحي `http://localhost:8000/app`

**٢) اتأكدي إن كل حاجة شغالة** (في تيرمينال تاني، والسيرفر شغال)

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe info/check_dashboard.py
```

**٣) اتأكدي إن كل زرار موصول**

```bash
cd C:/Users/Admin/Desktop/dev/scraper_v01 && C:/Users/Admin/.conda/envs/marketing_scraper/python.exe info/check_wiring.py
```

الاتنين بيكتبوا في `logs/dashboard_check.log` — يعني لو حاجة وقعت وانتي مش قاعدة، هتلاقي السبب مكتوب.

---

## أهم حاجة تعرفيها دلوقتي

**مشروع جوجل متقفل من الفوترة.** كل نداء لـVeo أو Gemini بيرجّع:

```
403 PERMISSION_DENIED — "Lightning dunning decision is deny for project: projects/627765796315"
```

«Dunning» = تحصيل فواتير. يعني **الحساب موقوف، مش كوتا ولا صلاحيات**.

**اللي بيتأثر:** توليد الريل · توليد البوستر · اكتشاف منافسين جدد · الناقد الفني · بوابات جودة الصور.
**اللي لسه شغال ١٠٠٪:** الداشبورد · الـSWOT من المخزون · المنافسين المسجّلين · السوشيال ميديا ·
**معاينة البرومبت والتكلفة قبل الصرف** · كل الفحوصات.

الحل مش عندي — لازم تدخلي [Google Cloud Billing](https://console.cloud.google.com/billing).
لو عندك مشروع تاني شغال، غيّري سطر واحد في `.env`:

```
GOOGLE_CLOUD_PROJECT=<اسم المشروع التاني>
```

وبعدين **أعيدي تشغيل السيرفر** (الكود بيتقرا مرة واحدة عند التشغيل).
