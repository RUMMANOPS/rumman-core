# Mobile Data Source Audit
**Date:** 2026-06-23
**Mode:** Audit only — لا تعديل، لا code changes، لا DB writes، لا deploy، لا commit.
**Scope:**
- Mobile: `/Users/ibrahim../Projects/0-RUMMAN/rumman-mobile/rumman-mobile`
- Backend: `/Users/ibrahim../Projects/0-RUMMAN/RUMMAN-Platform/rumman-core`
**Method:** Read every data-touching file (Onboarding, Profile, Courses, Today, Calendar, Registration, all `services/`, contexts, components). Verified the inst↔cat discrepancy live against Supabase. No claim below is unverified.

---

## 0. الخلاصة التنفيذية (TL;DR)

السبب الجذري لكل البيانات الخاطئة (126 بدل 130، التخصص/الكلية غير الرسمية، كل المستويات) **مصدر واحد:**

> الـ Onboarding يقرأ من الجداول القديمة `inst_colleges` / `inst_specializations` / `inst_courses` مباشرة عبر Supabase anon key، **يحسب الساعات في العميل** بافتراضات، يضيف **fallbacks ثابتة (126، 8، 3)**، ثم **يجمّد الناتج** كـ blob واحد في `student_context` + AsyncStorage. كل شاشة Profile/GPA تقرأ هذا الـ blob المجمّد للأبد. **لا الكتالوج الرسمي ولا Progress API يُستخدمان إطلاقًا.**

| البُعد | الحالة |
|--------|--------|
| Catalog API مستخدَم في الموبايل؟ | **لا — صفر استدعاءات** |
| Progress API مستخدَم في الموبايل؟ | **لا — صفر استدعاءات** |
| مصدر الكلية/التخصص/الساعات/المستويات | `inst_*` (legacy) + hardcoded fallbacks |
| Courses / Today / Calendar | ✅ نظيفة — Railway API فقط |
| Registration | يقرأ `term_sections` مباشرة (تشغيلي، مقبول) + Railway API |

---

## 1. خريطة مصادر البيانات الحالية

### آلية الهوية والتخزين
- **الهوية:** `services/auth.js` → device UUID → `POST /v1/auth/identify` → `student_id` (من `rumman_users`, platform='mobile'). سليم.
- **تخزين الـ Profile:** `OnboardingScreen` يبني object → `saveProfile()` (`PUT /v1/student/{id}/profile`) → backend `auth_api.py` يخزّنه كـ **blob خام** في `student_context (context_type='onboarding_profile')` + نسخة في AsyncStorage (`@rumman:profile_cache_v1`).
- **قراءة الـ Profile:** `getProfile()` (`GET /v1/student/{id}/profile`) يعيد نفس الـ blob كما هو. **لا إعادة حساب من cat_* أو progress.** (`auth_api.py:202-208, 298-309`)

### جدول المصادر لكل شاشة

| الشاشة | المصدر | التفاصيل | الحكم |
|--------|--------|----------|-------|
| **Onboarding** | 🔴 Supabase مباشر (`inst_*`) | `services/supabase.js`: `fetchColleges`→`inst_colleges`، `fetchSpecializations`→`inst_specializations`، `fetchPlanCourses`→`inst_courses`. حساب الساعات client-side. | **مصدر المشكلة** |
| **Profile** | 🔴 blob مجمّد | `getProfile()` → الـ blob من onboarding. يعرض total/completed/remaining hours + level/numLevels. | يعرض بيانات قديمة |
| **GPACalculator** | 🔴 blob مجمّد | `components/GPACalculatorModal.js:223` → `profile?.completed_credit_hours \|\| 0`. | يبني على blob خاطئ |
| **Courses** | 🟢 Railway API | `getRegisteredSections(confirmedOnly)` → `data.courses`. credit_hours من API. (بطاقة MGT201 dev معزولة، ليست fallback) | نظيف |
| **Today** | 🟢 Railway API | `getToday` / `getCalendar` / `getTasks`. لا بيانات أكاديمية ثابتة. | نظيف |
| **Calendar** | 🟢 Railway API | `getCalendar` / `getTasks`. ⚠️ `TOTAL_WEEKS = 16` ثابت (يؤثر على مؤشر الأسبوع فقط، يفشل blank لا fabricated). | نظيف (تحذير بسيط) |
| **Registration** | 🟡 مختلط | `fetchTermSections`→`term_sections` (Supabase مباشر، بيانات تشغيلية مقبولة) + Railway API (pin/confirm/plan). **الترم من backend لا hardcoded** (`/config/active-term`). | مقبول |
| **registrationEngine** | 🟢 دوال نقية | لا استدعاءات. ⚠️ fallback `creditHours ?? 0` (سطر 249، 613) قد يحسب مقرر بـ 0 ساعة صامتًا لو نقصت البيانات. | تحذير بسيط |

---

## 2. الملفات التي تحتوي hardcoded / mock / old data

| الملف:السطر | المحتوى | الخطورة |
|-------------|---------|---------|
| `screens/OnboardingScreen.js:107` | `total_credit_hours: selectedSpecialization.total_credits \|\| 126` | 🔴 **حرجة** — مصدر "126 بدل 130" |
| `screens/OnboardingScreen.js:108` | `num_levels: selectedSpecialization.num_levels \|\| 8` | 🔴 حرجة — مصدر "كل المستويات" |
| `screens/OnboardingScreen.js:90,94,133` | `credit_hours \|\| 3` (افتراض 3 ساعات لأي مقرر بلا قيمة) | 🟠 يشوّه حساب الساعات المنجزة/المتبقية |
| `screens/OnboardingScreen.js:68-71` | الساعات المنجزة **مُفترَضة** من المستوى (`level < selectedLevel` ⇒ مجتاز) | 🔴 حرجة — رقم "منجز" مخترَع لا حقيقي |
| `screens/OnboardingScreen.js:212-213` | قائمة المستويات `1..(num_levels\|\|8)` من inst | 🔴 حرجة — مصدر عرض كل المستويات |
| `screens/ProfileScreen.js:79` | `num_levels \|\| 8` | 🔴 يكرّر الافتراض عند العرض |
| `services/supabase.js:24-43` | `fetchColleges/Specializations/PlanCourses` → `inst_*` | 🔴 **الجذر** — المصدر غير الرسمي |
| `components/GPACalculatorModal.js:223` | `completed_credit_hours \|\| 0` | 🟠 يبني على blob خاطئ |
| `screens/CalendarScreen.js:155` | `TOTAL_WEEKS = 16` | 🟡 مؤشر الأسبوع فقط |
| `services/registrationEngine.js:249,613` | `creditHours ?? 0` / `\|\| 0` | 🟡 احتمال 0-credit صامت |
| `screens/CoursesScreen.js:89-113` | بطاقة `MGT201` "Marketing Management" | 🟢 dev معزولة، ليست fallback، لا تُشحن |

**ملاحظة:** `data/p0_5/mgt201/*.json` (training seeds) لا تُستخدم كمصدر بيانات أكاديمية للملف الشخصي — تخص تدريب MGT201 فقط.

---

## 3. أين سبب مشكلة الساعات والتخصص (مثبت بالـ DB)

قارنت `inst_specializations` (ما يقرأه الموبايل) مقابل `cat_programs` (المصدر الرسمي الذي اجتاز smoke tests):

| program_code | inst (الموبايل يعرض) | cat الرسمي | الفرق |
|--------------|---------------------|-----------|-------|
| MGT | **126** | **130** | ❌ −4 |
| CS | 126 | 130 | ❌ −4 |
| IT | 126 | 130 | ❌ −4 |
| ACC | 126 | 130 | ❌ −4 |
| FIN | 126 | 130 | ❌ −4 |
| PH | NULL → **126** (fallback) | **133** | ❌ −7 |
| DS | NULL → 126 | 133 | ❌ −7 |
| DM | NULL → 126 | 125 | ❌ +1 |
| ECOM | NULL → 126 | 130 | ❌ −4 |
| HCI | NULL → 126 | 130 | ❌ −4 |
| ENGT | NULL → 126 | 127 | ❌ −1 |
| MBA | **42** | **36** | ❌ −6 |
| EMBA | 42 | 36 | ❌ −6 |
| LAW | 156 | 128 | ❌ (لكن LAW مستثناة من Progress أصلًا) |
| MCS/MDS/MHA/MTT/EMHQS | 36 | 36 | ✅ تطابق |

**النتيجة:**
- **الساعات:** كل برامج البكالوريوس + MBA/EMBA خاطئة في الموبايل. التخصصات ذات `total_credits=NULL` في inst (PH, DS, DM, ECOM, HCI, ENGT) تظهر **126 مخترَع** بدل قيمتها الحقيقية.
- **التخصص/الكلية:** تأتي من `inst_colleges`/`inst_specializations` — تتضمن LAW (156، خاطئة) و GEN و MBADM (لا مقابل في الكتالوج)، وتفتقد الدبلومات. الكتالوج الرسمي يستثني LAW والدبلومات تلقائيًا (مثبت: Student E → 404).
- **المستويات:** `cat_programs.num_levels = NULL` للجميع (الكتالوج لا يخزّن العدد على مستوى البرنامج — يُشتق من مستويات المقررات عبر `/plan`). الموبايل يثبّت 8/4 من inst → يعرض مستويات قد لا تطابق البنية الرسمية.

**التطابق الحاسم:** أكواد inst (CS, IT, MGT, FIN, ACC, PH, DM, DS, ECOM, HCI, ENGT, MBA, EMBA, MCS, MDS, MHA, MTT, EMHQS) **تطابق `cat_programs.program_code`** — أي `specialization_code` من onboarding صالح مباشرةً كـ `program_code` للكتالوج/Progress. الاستثناءان الوحيدان: `GEN` (ليست برنامجًا) و `MBADM` (لا مقابل في الكتالوج → ستُعطي 404).

---

## 4. ما الذي يجب أن يأتي من Catalog API

الكتالوج بيانات مرجعية بحتة — **لا يحتاج صفوفًا لكل طالب**، جاهز للربط فورًا:

| الحاجة | endpoint | يستبدل |
|--------|----------|--------|
| قائمة الكليات | `GET /v1/catalog/programs` ثم تجميع حسب `college_code` | `fetchColleges()` → `inst_colleges` |
| البرامج لكل كلية | `GET /v1/catalog/programs` مفلتر بـ `college_code` | `fetchSpecializations()` → `inst_specializations` |
| **ساعات البرنامج الرسمية** | `total_credits_official` من نفس الاستجابة | `total_credits \|\| 126` ← **يصلح 126/130** |
| بنية المستويات الرسمية | `GET /v1/catalog/programs/{code}/plan` | `num_levels \|\| 8` + `inst_courses.level` |
| مقررات الخطة لكل مستوى | `GET /v1/catalog/programs/{code}/courses` | `fetchPlanCourses()` → `inst_courses` |
| استثناء LAW/الدبلومات | تلقائي في الكتالوج | يدوي حاليًا (`code !== 'GENERAL'`) |

**فائدة جانبية:** الانتقال للكتالوج يستثني LAW والدبلومات تلقائيًا، ويُظهر علم `needs_review` لـ PH/FIN.

---

## 5. ما الذي يجب أن يأتي من Progress API

| الحاجة | endpoint | يستبدل |
|--------|----------|--------|
| الساعات المنجزة | `GET /v1/progress/summary` → `completed_credits` | الحساب client-side المُفترَض من المستوى |
| الساعات المتبقية | `/summary` → `remaining_credits` | `remaining_credit_hours` المُفترَض |
| المقررات المنجزة | `GET /v1/progress/completed` | قائمة `completed_courses` المُفترَضة |
| المقرر الحالي | `GET /v1/progress/current` | (Courses يستخدم registered-sections حاليًا) |
| المستوى/حالة الخطة | `GET /v1/progress/plan-status` | `current_level` اليدوي |
| علم needs_review | كل endpoints | غير موجود |

---

## 6. خطة التنفيذ — 3 مراحل

### المرحلة 1 — إصلاح مصدر Profile/Courses (Catalog) — **جاهزة الآن، لا فجوة**
1. أضف عميل Catalog في `services/` (`getPrograms`, `getProgram(code)`, `getProgramPlan(code)`).
2. `OnboardingScreen`: استبدل `fetchColleges/Specializations/PlanCourses` بـ Catalog. اشتق الكليات بتجميع `/programs` حسب `college_code`.
3. احذف fallbacks `\|\| 126` و `\|\| 8` و `\|\| 3` — استخدم `total_credits_official` و `/plan` الرسمية.
4. خزّن `program_code` (= `specialization_code`) صراحةً في الـ profile blob.
5. `ProfileScreen`/`GPACalculator`: اقرأ `total_credit_hours` من القيمة الرسمية المخزّنة.
6. **الأثر:** يصلح فورًا "126 بدل 130"، التخصص/الكلية الرسمية، استثناء LAW/الدبلومات، وبنية المستويات. **بدون أي تغيير backend.**

### المرحلة 2 — ربط Progress — **محجوبة بفجوة backend (انظر §7/§8)**
1. (backend) جسر ينشئ `student_program_profile (student_id, program_code, is_active)` من اختيار البرنامج في onboarding.
2. (backend) مسار يملأ `student_course_history` من المقررات المنجزة المُعلَنة ذاتيًا (أو قبول أنها self-declared حتى يصل banner_sync للموبايل).
3. (mobile) `ProfileScreen` يقرأ `completed/remaining` من `/v1/progress/summary` بدل الحساب المحلي.
4. **الأثر:** الساعات المنجزة/المتبقية تصبح حقيقية لا مُفترَضة.

### المرحلة 3 — Today redesign لاحقًا
- بعد استقرار Catalog+Progress، أعد تصميم Today ليعرض "ناقصك للتخرج" من Progress مباشرة. خارج نطاق هذا الـ audit.

---

## 7. المخاطر قبل التعديل

| # | الخطر | الأثر | التخفيف |
|---|-------|------|---------|
| R1 | **Progress يحتاج `student_program_profile` — onboarding لا ينشئه** | كل طالب جوال حقيقي → 404 من Progress | جسر backend (المرحلة 2) قبل أي ربط Progress |
| R2 | **Progress يحتاج `student_course_history` — لا يوجد للموبايل** | `/completed` و`/summary` فارغة/خاطئة | تعريف مصدر التاريخ (self-declared أو banner_sync) |
| R3 | `MBADM` و`GEN` بلا مقابل في الكتالوج | اختيارهما → 404 | إخفاؤهما من قائمة الكتالوج (تلقائي لمعظمها) |
| R4 | profiles مخزّنة حاليًا بـ 126/inst | المستخدمون الحاليون يبقون على القديم بعد التحديث | إعادة جلب/ترحيل الـ blob عند أول فتح بعد التحديث |
| R5 | الموبايل يحمل `service_role` key في `.env` | تسريب صلاحيات كاملة لو بُني في bundle | (خارج النطاق لكن يجب إزالته — العميل يجب أن يستخدم anon فقط أو لا Supabase مباشر) |
| R6 | `TOTAL_WEEKS=16` و`creditHours ?? 0` | مؤشر أسبوع/إجمالي ساعات جدول مضلّل | إصلاحات صغيرة لاحقة، غير مانعة |

---

## 8. القرار

**`NEEDS_BACKEND_GAP_BEFORE_MOBILE_CONNECT`**

**السبب:** الربط الكامل (Catalog **و** Progress) محجوب بفجوة backend حقيقية: Progress API يقرأ `student_program_profile` + `student_course_history`، و onboarding الحالي **لا ينشئ أيًّا منهما** (يكتب blob في `student_context` فقط). بدون جسر، كل طالب جوال حقيقي يحصل على 404 من Progress.

**لكن بتقسيم دقيق (لا تكن منفذًا أعمى):**

- **المرحلة 1 (Catalog فقط) = جاهزة للتنفيذ الآن بلا أي فجوة.** هي وحدها تصلح كل الأعراض المُبلَّغة (126/130، التخصص/الكلية، المستويات) لأنها بيانات مرجعية لا تحتاج صفوفًا لكل طالب، والأكواد تتطابق أصلًا.
- **المرحلة 2 (Progress) = محجوبة** حتى يُبنى جسر `student_program_profile` + مصدر `student_course_history` للموبايل.

**التوصية:** اعتمد المرحلة 1 (Catalog) كعمل مستقل فوري، وافتح بند backend منفصل للجسر (المرحلة 2) قبل أي وعد بربط Progress.
