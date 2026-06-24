# Next Implementation Plan — 3 Waves (draft, no execution)

**التاريخ:** 2026-06-24
**المرجع:** Decision Matrix + Gate Spec + Patch Backlog (نفس المجلد).
**حالة التنفيذ:** **خطة فقط.** لا DB writes، لا migration apply، لا mobile/backend code، لا commit/push/deploy.

---

## القرار المعماري المحوري أولًا: أين يقع الـ Gate؟

| الخيار | المزايا | العيوب |
|--------|---------|--------|
| **Gate مؤقت في الموبايل (ثابت `CERTIFICATION`)** | أسرع للإطلاق، لا يحتاج backend، لا migration | يتطلب **إعادة نشر App Store** عند اعتماد أي برنامج؛ يَدْرِف عن الكتالوج |
| **Gate من API (حقل `certification` في `/programs`)** | اعتماد برنامج = تحديث خادم بلا نشر تطبيق؛ مصدر حقيقة واحد | يحتاج عمل backend (وربما migration) |

**التوصية:** **هجين متدرّج.**
- **Wave A (الآن):** gate مؤقت في الموبايل بثابت `CERTIFICATION` (15 تظهر خطتها، 4 محجوبة) — لإطلاق سريع وآمن.
- **Wave B:** نقل المصدر إلى API (`certification` block مشتق من سجل اعتماد على الخادم) فيقرأه الموبايل ديناميكيًا.
- **السبب:** نطلق بأمان فورًا دون انتظار backend، ثم نزيل دَيْن «إعادة النشر لكل اعتماد» بنقل الـ gate للـ API. الثابت المؤقت يُعلَّم `// TEMP: replace with API field (Wave B)`.

**هل نحتاج migration الآن؟ لا.** الـ gate لا يتطلب schema change. حقل API في Wave B يمكن اشتقاقه من **سجل اعتماد خفيف** (جدول صغير أو config على الخادم) دون تعديل `cat_programs`. تعديل `cat_programs` (إضافة `certification_status`/`academic_phase`/`structure_type`/provenance) هو **Wave B/P1** للمتانة، وأقترح له **DRAFT migration حينها فقط** — وليس الآن.

---

## Wave A — Mobile Gate Now (إطلاق آمن)

**الهدف:** منع ظهور أي خطة مكسورة، وإطلاق 15 برنامجًا.

**ملفات ستتغير لاحقًا (موبايل):**
- `services/catalogApi.js` — قراءة/تمرير حقل `certification` (أو دمج الثابت المؤقت).
- `constants/certification.js` (جديد، مؤقت) — خريطة `CERTIFICATION` من Decision Matrix JSON.
- `screens/OnboardingScreen.js` — استخدام `official_total_credits`؛ علَم السنة الأولى المشتركة؛ الاختياريات كمجموعة.
- `screens/ProgressMap`/`ProfileScreen.js` — تطبيق `planView()`: عرض «قيد التحقق» للمحجوبة، «السنة الأولى المشتركة»، كتلة الاختياريات.
- `screens/Registration` — تطبيق `canSmartRegister()` (منع level ≤2 وغير المعتمد والماجستير).

**نطاق Wave A:**
- 8 certified → خطة كاملة.
- 7 semantics → خطة + (سنة أولى مشتركة / ساعات رسمية / اختياريات).
- 4 (HCI/PH/MHA/ENGT) → برنامج يظهر، خطة «قيد التحقق».
- Smart Registration: مفعّل فقط للبكالوريوس المعتمد ≥ مستوى 3.

**لا migration. لا backend. ثوابت موبايل مؤقتة فقط.**

---

## Wave B — Catalog Schema/Data Patch (متانة)

**الهدف:** نقل الاعتماد للخادم + إصلاح البيانات المكسورة.

**عناصر:**
1. **API field:** إضافة `certification` block لاستجابة `/v1/catalog/programs` و`/programs/{code}` (backend code).
2. **schema (DRAFT migration حينها فقط):** أعمدة في `cat_programs`:
   - `certification_status`, `plan_display_status`, `academic_phase` (`common_first_year|standard|year_based`), `program_structure_type` (`semester|year`)
   - `source_url`, `source_sha256`, `source_acquired_at` (provenance)
   - تفعيل `num_levels`
   - على `cat_program_courses`: `is_first_year`, دعم `choose_rule` للاختياريات (موجود جزئيًا).
3. **data patches (P1):** HCI (استعادة L2، de-bloat L8، 151→130)؛ PH (نمذجة فصلية أو وسم سنوي، 151→133)؛ ENGT (مصالحة L2/L3)؛ MHA (42→36)؛ backfill provenance.

**هل نحتاج migration؟ نعم في Wave B** (لأعمدة الاعتماد/provenance). **هل نحتاج backend API fields؟ نعم** (كشف `certification`). أقترح إنشاء **DRAFT migration** عند بدء Wave B — وأذكر صراحةً: **غير مطلوب الآن.**

---

## Wave C — Re-certification & App Store Readiness

**الهدف:** إعادة تشغيل الـ Audit بعد الإصلاحات وإصدار قائمة الجهوزية.

**عناصر:**
1. إعادة تشغيل `FULL_CATALOG_PLAN_CERTIFICATION_AUDIT` على البرامج المُصلَحة (HCI/PH/MHA/ENGT).
2. ترقية حالتها في Decision Matrix من `hide_plan_until_patch` إلى `certified_100`/`safe_with_semantics`.
3. تحديث سجل الاعتماد على الخادم (بلا نشر تطبيق — بفضل Wave B).
4. إخراج «قائمة البرامج الجاهزة للإطلاق» المحدّثة.
5. تأهيل Smart Registration للبرامج المؤهّلة.

**ملفات ستتغير:** سجل الاعتماد (خادم)، تقرير Audit محدّث، Decision Matrix محدّث. **لا تغيير موبايل** (لأن الـ gate صار من API).

---

## القرار الموصى به ولماذا

**ابدأ Wave A فورًا بـ gate مؤقت في الموبايل.** لأن:
1. يطلق 15 برنامجًا بأمان دون انتظار backend/migration.
2. يحمي الطالب من 4 خطط مكسورة بحجب رشيق («قيد التحقق») لا بإخفاء البرنامج.
3. إصلاحات البيانات (Wave B) تجري بالتوازي بعد الإطلاق — لا تمنعه.
4. Wave B ينقل الـ gate للـ API فيزيل دَيْن إعادة النشر، وحينها فقط ننشئ DRAFT migration.

**البديل المرفوض:** حجب كل الموبايل حتى اعتماد كامل الكتالوج — يؤخّر إطلاق 15 برنامجًا سليمًا بلا داعٍ.
