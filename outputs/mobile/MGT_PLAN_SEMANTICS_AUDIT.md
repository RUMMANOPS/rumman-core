# MGT Plan Semantics Audit

**التاريخ:** 2026-06-24  
**النطاق:** فحص بنية خطة MGT من المصدر الرسمي حتى الـ API — لا تعديل.  
**الطلب:** تشخيص سبب غياب Level 2 وعلاقة ENG001 بالسنة الأولى.

---

## 1. المصدر الرسمي المستخدم لخطة MGT

| المصدر | الملف | التاريخ | الحالة |
|--------|-------|---------|--------|
| خطة MGT الرسمية (الأحدث) | `study-plan-ba-dec-2025.pdf` | ديسمبر 2025 | المرجع الحاكم |
| خطة MGT الرسمية (الأقدم) | `برنامج بكالوريوس العلوم في إدارة الأعمال - تخصص إدارة.pdf` | ديسمبر 2025 | متطابق مع الأحدث |
| الكتالوج (migration 071) | `071_official_catalog_seed.sql` | 2026-06 | مستخرج من المصدر أعلاه |

كلا الملفين عنوانهما: **BACHELOR OF SCIENCE IN BUSINESS ADMINISTRATION (BSBA) - MAJOR IN MANAGEMENT**، كلية العلوم الإدارية والمالية، الجامعة السعودية الإلكترونية.

---

## 2. كيف تظهر السنة الأولى في المصدر الرسمي

صفحة **"Program Structure by Levels"** في الخطة الرسمية (Dec 2025):

### First Year (السنة الأولى المشتركة)

| # | Code | Title | Cr | Pre-requisites |
|---|------|-------|----|----------------|
| 1 | ENG001 | English Language Skills | 8 | — |
| 2 | CS001 | Computer Essentials | 3 | — |
| 3 | COMM001 | Communication Skills | 2 | — |
| 4 | ENG001 | English Language Skills (Continued) | 8 | — |
| 5 | MATH001 | Fundamentals of Math | 3 | — |
| 6 | CI001 | Academic Skills | 2 | — |

**المجموع: 26 ساعة — 6 مقررات (ENG001 مرتين)**

المصطلح الرسمي في الخطة ديسمبر 2025: **"First Year"** وشرط الانتقال في Level 3 هو **"Common First Year"**.  
المصطلح في الخطة الأقدم: **"Passing First Year"** كشرط مسبق.

**الاسم المؤسسي الرسمي:** السنة الأولى المشتركة — مشتركة بين جميع برامج كلية العلوم الإدارية والمالية.

---

## 3. هل Level 2 موجود رسميًا أم لا

### النتيجة: **Level 2 غائب من المصدر الرسمي تمامًا وبشكل متعمد.**

الخطة الرسمية تنتقل مباشرةً من:

> **First Year** → **Level Three** → Level Four → Level Five → Level Six → Level Seven → Level Eight

لا يوجد **Level Two** في أي نسخة من الخطة الرسمية. هذا تصميم أكاديمي مقصود وليس خطأ استخراج.

الدليل المباشر من المصدر: جدول "Program Structure by Levels" في الملف الرسمي يبدأ بـ "First Year" ثم "Level Three" بالحروف.

---

## 4. لماذا API يعرض [1, 3, 4, 5, 6, 7, 8]

### السلسلة: المصدر → DB → API

```
PDF official (Dec 2025)
  "First Year"        → extractor → level = 1  (mapping حرفي)
  "Level Three"       → extractor → level = 3  (رقم حرفي من الملف)
  "Level Four"        → extractor → level = 4
  ...
  "Level Eight"       → extractor → level = 8
```

المستخرج (`_071_catalog_loader.py`) رأى "First Year" → أسنده رقم 1 (الأقرب منطقيًا).  
رأى "Level Three" → أسنده 3.  
**لم يخترع Level 2 لأن المصدر لا يحتويه.** هذا قرار صحيح من المستخرج.

نتيجة الـ API `/v1/catalog/programs/MGT/plan`:
```
Level 1:  [CI001, COMM001, CS001, ENG001, MATH001]   26cr  ← "First Year" الرسمي
Level 3:  [ACCT101, ECON101, ISLM101, LAW101, MGT101, STAT101]  17cr
Level 4:  [ECOM101, FIN101, ISLM102, MGT201, MGT211, STAT201]   17cr
Level 5:  [ECOM201, ECON201, MGT301, MGT311, MGT312, MIS201]    18cr
Level 6:  [ACCT301, ISLM103, MGT321, MGT322, MGT323 + 3 electives]  23cr
Level 7:  [ISLM104, MGT324, MGT401, MGT402, MGT403]             14cr
Level 8:  [MGT404, MGT421, MGT422, MGT430]                      15cr
```

**البيانات صحيحة. لا drift. لا extraction error.**

---

## 5. تفسير ENG001 وسبب 16 ساعة

### ENG001 = مقررَيْن متتاليَيْن في جزأين، كلاهما في السنة الأولى

| Row | Code | Title | Cr |
|-----|------|-------|----|
| 10 | ENG001 | English Language Skills | 8 |
| 11 | ENG001 | English Language Skills (Continued) | 8 |

مجموع: **16 ساعة معتمدة**

هذا الرقم مصدره الخطة الرسمية حرفيًا. ENG001 يمثل برنامج اللغة الإنجليزية التأسيسي للسنة الأولى المشتركة، مقسمًا على سداسين.

الكتالوج دمجهما في صف واحد (`credit_hours = 16`) مع `official_raw_text` يوثّق ذلك:
```
'10 | ENG001 | English language Skills | 8 | + 11 | ENG001 | English language Skills (Continued) | 8 |'
```

**هذا تمثيل صحيح للمصدر. لا يجوز تقسيمه إلى صفين لأن** `cat_courses` يستخدم `canonical_course_code` مفتاح فريد، ودمجهما بـ 16cr هو القرار الصحيح معلوماتيًا.

**ENG001 ليس "كتلة إنجليزية" مجهولة المصدر — هو مقررٌ جامعي معتمد بـ 16cr رسميًا في خطة البكالوريوس.**

---

## 6. هل المشكلة عامة على برامج أخرى

### نعم — نمط [1, 3, 4, 5, 6, 7, 8] موجود في 6 برامج على الأقل

| Program | Levels in DB | ENG001 Cr | ملاحظة |
|---------|-------------|-----------|--------|
| MGT | [1,3,4,5,6,7,8] | 16 | مؤكد من مصدر رسمي |
| ACC | [1,3,4,5,6,7,8] | 16 | raw_text: "First Year" |
| FIN | [1,3,4,5,6,7,8] | يُرجَّح 16 | خطة الكلية موحدة |
| ECOM | [1,3,4,5,6,7,8] | 16 | مصدر: مصفوفة ADMIN |
| IT | [1,3,4,5,6,7,8] | 16 | raw_text: row-seq format |
| DS | [1,3,4,5,6,7,8] | 8 | كلية COMP — نفس النمط |

البرامج التي لها level 2 (البنية الكاملة [1,2,3,...,8]):

| Program | Levels | ملاحظة |
|---------|--------|--------|
| CS | [1,2,3,4,5,6,7,8] | ENG001 = 8cr فقط (مقرر واحد) |
| DM | [1,2,3,4,5,6,7,8] | لا ENG001 |
| ENGT | [1,2,3,4,5,6,7,8] | ENG001 = 16cr لكن Level 2 موجود |

**الخلاصة:** النمط الغائب لـ Level 2 خاص ببرامج السنة الأولى المشتركة ADMIN + بعض برامج COMP. هو سمة أكاديمية مؤسسية وليس خللًا في البيانات.

---

## 7. ما الخطر إذا رقعناها في الموبايل

| الرقعة المقترحة | الخطر |
|----------------|-------|
| إنشاء Level 2 افتراضي (حشو) | **خطر عالٍ** — يتناقض مع المصدر الرسمي، يضلّل الطالب، يكسر القاعدة الحاكمة |
| تحويل Level 1 إلى "Level 2" في الـ display | **خطر عالٍ** — الطالب في المستوى 1 يعرف نفسه "سنة أولى" لا "مستوى 2" |
| إخفاء Level 1 من واجهة التقدم | **خطر متوسط** — يُغفل 26 ساعة من الخطة وهذا مضلل |
| تغيير `computeInferredNextLevel` لتجاهل الفجوة | **لا خطر** — الكود الحالي يتعامل معها صحيحًا بالفعل |
| عرض "السنة الأولى" بدلاً من "المستوى 1" | **لا خطر — الحل الصحيح** |

**القاعدة:** أي رقعة تختلق Level 2 أو تُعيد ترقيم المستويات هي خيانة للمصدر الرسمي.

---

## 8. الخيار الصحيح للإصلاح

### الحالي (يعمل صحيحًا بدون تغيير):
- `computeInferredNextLevel` يستخدم `levelsInPlan.find(l => l > declaredLevel)` ← يقفز صحيحًا من 1 إلى 3.
- API يعيد [1,3,4,5,6,7,8] ← صحيح.
- DB بيانات صحيحة ← لا migration مطلوب.

### ما يحتاجه الموبايل فقط — display rule:

```javascript
function getLevelDisplayLabel(level, levelsInPlan) {
  // المستوى 1 في برامج ADMIN = "السنة الأولى المشتركة"
  // نعرفها من كونها أول مستوى والتالي له هو 3
  const isCommonFirstYear = level === 1 && levelsInPlan.includes(3) && !levelsInPlan.includes(2);
  if (isCommonFirstYear) return 'السنة الأولى المشتركة';
  return `المستوى ${level}`;
}
```

هذا **mobile display rule** — لا يغير البيانات، لا يحتاج migration، لا يحتاج API تغيير.

### ما يحتاجه الكتالوج على المدى المتوسط — schema enhancement:

إضافة حقل `level_type ENUM('common_first_year', 'regular', 'capstone')` إلى `cat_program_courses` أو حقل `academic_phase TEXT` إلى `cat_programs`.

هذا **ليس blocking للموبايل الآن** — البيانات الحالية كافية لعرض صحيح بـ display rule.

### ما لا يُفعل:
- لا إنشاء Level 2 في DB.
- لا إعادة ترقيم المستويات.
- لا تغيير credit_hours لـ ENG001.
- لا تقسيم ENG001.

---

## ملخص الإجابات على الأسئلة الحاسمة

| السؤال | الإجابة |
|--------|---------|
| **1. هل Level 2 غائب من المصدر الرسمي أم غاب أثناء الاستخراج؟** | **غائب من المصدر الرسمي تمامًا.** الخطة تقول: First Year ← Level Three. الاستخراج صحيح 100%. |
| **2. هل ENG001 يمثل السنة الأولى الإنجليزية كاملة؟** | **نعم** — مقررٌ في جزأين (8+8=16cr)، كلاهما في "First Year" رسميًا. ليس كتلة — هو مقرر جامعي معتمد. |
| **3. هل الخطة تحتاج نموذج semantics إضافي؟** | **نعم على المدى المتوسط:** `level_type = 'common_first_year'` لـ Level 1 في ADMIN programs. لكنه ليس blocking الآن. |
| **4. هل نحتاج migration لإصلاح cat_program_courses.level؟** | **لا.** البيانات صحيحة تمامًا. Level=1 يعكس "First Year" الرسمي بدقة. |
| **5. هل المشكلة محصورة في MGT؟** | **لا.** ACC, ECOM, FIN, IT, DS — كل برامج السنة الأولى المشتركة لها نفس البنية [1,3,...,8]. |
| **6. هل /plan يجب أن يعرض level_type؟** | **مطلوب مستقبلًا** — لكن حاليًا display rule في الموبايل يكفي. |

---

## 9. القرار

```
MOBILE_CAN_HANDLE_WITH_DISPLAY_RULE
```

**التفسير:**
- البيانات في DB والـ API صحيحة وتعكس المصدر الرسمي.
- لا migration مطلوب، لا backend change مطلوب.
- `computeInferredNextLevel` يتعامل مع الفجوة 1→3 صحيحًا بالفعل.
- الإصلاح الوحيد المطلوب: **عرض "السنة الأولى المشتركة" بدلاً من "المستوى 1"** في واجهة onboarding وواجهة التقدم.
- هذا display rule بسيط في الموبايل، لا يمس البيانات.

**الإصلاح الثانوي المؤجل (Wave 2+ أو Catalog v2):**
- إضافة `level_type` field إلى `cat_program_courses` لتمييز السنة الأولى المشتركة برمجيًا دون rely على inference.

---

## ملحق: ENG001 في برامج أخرى للمقارنة

| Program | ENG001 raw_text | level في DB | cr |
|---------|----------------|------------|-----|
| MGT | `10\|ENG001\|…8\| + 11\|ENG001\|…8\|` | 1 | 16 |
| ACC | `First Year \| ENG001 \| English Skills \| 16 \|` | 1 | 16 |
| IT | `2 \| ENG001 \| English Language Skills \| 16 \| -` | 1 | 16 |
| CS | `8 \| ENG001 \| English Language Skills \| 8 \|` | 1 | 8 |
| ENGT | (with level 2 present) | 1 | 16 |

CS وحده له ENG001 = 8cr فقط (مقرر واحد لا جزأين) — وهو البرنامج الوحيد الذي له Level 2 في COMP college.
