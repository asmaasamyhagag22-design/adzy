# الداشبورد — شاشة بشاشة

`http://localhost:8000/app`

كل زرار تحت **اتأكد إنه موصول بكود موجود فعلاً** (`info/check_wiring.py`):
**٤٣ زرار، كلهم بيلاقوا الدالة بتاعتهم · ٢٤ مسار API، كلهم موجودين على السيرفر · صفر زرار ميت.**

الرمز ✅ = اتجرّب وشغال · ⚠️ = شغال بس محتاج حاجة · ❌ = واقف على الفوترة

---

## ١. الدخول

| الزرار | بيعمل إيه | |
|---|---|---|
| Sign up / Log in | `POST /api/auth/signup` أو `/login` | ✅ |
| Logout | بيمسح التوكن | ✅ |

* حسابك: `asmaasamyh22@gmail.com` (موجود بالفعل).
* لو طلّعك فجأة ⟸ التوكن خلص. سجّلي دخول تاني.

---

## ٢. الصفحة الرئيسية — البراندات

| الزرار | بيعمل إيه | |
|---|---|---|
| خانة الـURL + Scrape | `POST /api/scrape` — بيسحب الموقع ويعمل بروفايل | ✅ |
| كارت براند | بيفتح تفاصيله | ✅ |
| Delete | بيمسح البراند | ✅ |
| Studio | بيروح لاستوديو الإنشاء | ✅ |

**المخزّن دلوقتي:** أبو عوف · دار الفؤاد · النهدي · نانوكير · بيبسي · Volume Qatar

---

## ٣. تفاصيل البراند (٦ تابات)

| التاب | فيه إيه | |
|---|---|---|
| Overview | الاسم، الوصف، التواصل، **لينكات السوشيال** | ✅ |
| Services | العروض والخدمات المسحوبة | ✅ |
| Team | الفريق | ✅ |
| Partners | الشركاء | ✅ |
| Programs | البرامج | ✅ |
| Dashboard | نبرة الصوت + جاهزية | ✅ |

القسم بيختفي لو مفيش داتا — **مش عطل**، ده مقصود.

---

## ٤. الاستوديو — ٥ مراحل (الشريط اللي على الشمال)

### Trend
| الزرار | بيعمل إيه | |
|---|---|---|
| Refresh trends | `POST /api/brands/{dk}/trends` | ⚠️ محتاج Serper |

### SWOT ← **أهم شاشة**

| الزرار | بيعمل إيه | |
|---|---|---|
| **What gets the likes** (لوحة) | `GET /api/social/performance/{dk}` | ✅ **جديد** |
| **Competitors** (لوحة) | `GET/POST/DELETE /api/brands/{dk}/competitors` | ✅ |
| Generate SWOT | `POST /api/swot/generate` | ⚠️ الاكتشاف الجديد واقف؛ المسجّلين شغالين |
| Approve SWOT | `POST /api/swot/approve` | ✅ |
| Adopt analysis | بياخد نتيجة CLI محفوظة | ✅ |

**لوحة «What gets the likes»** بتعرض **٣ حالات مختلفة** بقصد:

| اللي هتشوفيه | يعني |
|---|---|
| «No social snapshot collected» | البراند ده **ماتسحبش** سوشيال أبداً |
| «…engagement was never measured» | اتسحب بس البوستات مافيهاش أرقام |
| أرقام + دروس | مقيس فعلاً |

الفرق بين الأولانيتين مهم — «ماجمعناش» غير «جمعنا وملقناش».

**الأرقام الحقيقية المتاحة دلوقتي:**

| البراند | بوستات | وسيط اللايكات | الأعلى |
|---|---|---|---|
| كوكاكولا | 15 | 314 | **113,409** (361× الوسيط) |
| بافلو برجر | 15 | 91 | 607 |
| نانوكير | 15 | 4 | 378 (94× الوسيط) |
| دار الفؤاد | 15 | 3 | 12 |

**لوحة Competitors:** اكتبي الاسم + الموقع ← Add.
**دي اللي بتملّي الضعف والتهديدات** — مالهمش وجود من غير مقارنة.

### Strategy · Marketing Plan · Calendar
| الزرار | بيعمل إيه | |
|---|---|---|
| Generate strategy | `POST /api/strategy/generate` | ⚠️ محتاج موديل |
| Generate plan | `POST /api/marketing-plan/generate` | ⚠️ محتاج موديل |
| Generate calendar image | `POST /api/calendar/generate-image` | ❌ الفوترة |

الترتيب مفروض: SWOT ← اعتماد ← Strategy ← Plan. الزراير بترفض بـ`409` لو الترتيب اتكسر.

---

## ٥. الاستوديو — الإنشاء

| الزرار | بيعمل إيه | |
|---|---|---|
| خانة البرومبت | وصفك بيوصل للموديل كما هو | ✅ |
| Styles (٥) | Gradient · Minimalist · Bold · Corporate · Creative | ✅ |
| Format (٤) | Social 1:1 · Hero 21:9 · Poster 2:3 · Ad card 4:5 | ✅ |
| **Language** | Auto · **العربية** · English | ✅ **اتصلح النهاردة** |
| Trend toggle | بيركب الكرييتف على أعلى ترند مخزّن | ✅ |
| Copy override | عنوان/نص/CTA بإيدك | ✅ |
| **Preview brief** | `GET /api/prompt-preview/{dk}` | ✅ **جديد — ومجاني** |
| Generate | بوستر أو ريل | ❌ الفوترة |

### زرار «Preview brief» — الفيتشر اللي سألتي عنه

بيعرض **كل حاجة النظام قرّرها قبل ما يصرف مليم**:

```
Language      Arabic          the brand trades in 'eg', whose customers speak Arabic
Register      Egyptian colloquial (عامية مصرية) — natural spoken Masri
Vertical      service
Look          cinematic lighting, cool clean colour palette, bright soft daylight…
Grade         clinical
Tempo         100 BPM · beat 0.6s
Shots         4 (3 generated)   15s cut, 4s minimum clip, minus 1.4s for the insert
Last beat     a rendered product insert (no model, no spend)
Estimated cost $7.20            upper bound: every generated shot bought at 6s
▸ The director's full brief (22,751 characters)
```

**مافيش أي نداء موديل** — كله مشتق من أدلة البراند. عشان كده **شغال دلوقتي رغم قفل الفوترة**،
وده بالظبط الوقت اللي محتاجة تعرفي فيه النظام ناوي على إيه.

**الأهم:** بيقولك **التكلفة قبل ما تدفعيها**.

---

## ٦. الإنفلونسر

| الزرار | بيعمل إيه | |
|---|---|---|
| Generate portrait | `POST /api/influencer/portrait` | ❌ الفوترة |
| Approve / Retry / Another look | إدارة البورتريه | ✅ |
| Generate poster | `POST /api/influencer/poster` | ❌ الفوترة |

البوستر **بيرفض** يشتغل قبل اعتماد البورتريه — مقصود.

---

## ٧. اللي مش موصول من الواجهة (وده صح)

`/api/run` · `/api/poster/from-profile` · `/api/swot/from-profile` · `/api/showcase`

دي واجهات **CLI/آلية**. الداشبورد بيستخدم بدايل بـJWT. **مش أعطال.**
