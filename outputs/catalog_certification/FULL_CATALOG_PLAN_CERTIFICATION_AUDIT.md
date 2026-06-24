# Full Catalog Plan Certification Audit

**التاريخ:** 2026-06-24
**النطاق:** كل البرامج الـ19 التي يعرضها `/v1/catalog/programs` للموبايل.
**المنهج:** مقارنة ثلاثية لكل برنامج: المصدر الرسمي (PDF/DOCX) ↔ DB (`cat_programs` + `cat_program_courses`) ↔ API (`/programs/{code}` + `/plan`). لا DB writes، لا migration، لا commit، لا deploy.
**ملاحظة تنفيذية:** المصادر الرسمية استُخرجت نصيًا (`pdftotext -layout` / `pymupdf` / فك docx) من مستودع الجامعة. الوكلاء الفرعيون حُرموا صلاحية القراءة، فأُجري الفحص مباشرةً على النصوص المستخرجة.

---

## 1. Executive Summary

فُحصت 19 خطة نشطة مقابل مصادرها الرسمية. **النتيجة ليست "كلها سليمة" ولا "كلها مكسورة" — بل ثلاث فئات واضحة:**

- **8 برامج certified_100** — البيانات تطابق المصدر 100% وقابلة للعرض مباشرة.
- **7 برامج needs_semantics_review** — البيانات صحيحة رسميًا لكنها تحتاج طبقة عرض (السنة الأولى المشتركة، و/أو الاختياريات كمجموعة اختيار). تظهر بتحذير.
- **4 برامج needs_source_review** — البيانات **لا تتصالح** مع المصدر (أخطاء استخراج/ساعات). يجب **حجبها** عن الموبايل حتى الإصلاح.
- **0 blocked نهائيًا** (كلها قابلة للإصلاح).

**أخطر اكتشاف منهجي:** لا يمكن استخدام قاعدة عرض عامة مبنية على "غياب المستوى 2". لأن MGT يَغيب منه المستوى 2 **رسميًا وصحيحًا** (سنة أولى مشتركة)، بينما HCI يَغيب منه المستوى 2 بسبب **خطأ استخراج** (المصدر يحوي «المستوى الثاني» صراحةً). قاعدة عامة مبنية على الفجوة ستُخفي خطأ HCI وتُضلّل الطالب. **قاعدة السنة الأولى يجب أن تُفعَّل بعلَم صريح لكل برنامج بعد الاعتماد — لا بالاستنتاج من الفجوة.**

**فجوة حوكمة عابرة لكل البرامج:** `source_document`, `source_url`, `source_sha256` = NULL في `cat_programs` لكل الـ19. لا provenance مسجّل في DB. `num_levels` = NULL للكل (الموبايل يشتقّه من الخطة — هشّ).

---

## 2. عدّاد الاعتماد

| التصنيف | العدد | البرامج |
|---------|-------|---------|
| **certified_100** | 8 | DM, MCS, MDS, EMHQS, MBA, EMBA, MDM, MTT |
| **needs_semantics_review** | 7 | MGT, ACC, FIN, ECOM, CS, IT, DS |
| **needs_source_review** | 4 | HCI, PH, MHA, ENGT |
| **blocked** | 0 | — |

**Mobile display:**

| الحالة | العدد | البرامج |
|--------|-------|---------|
| safe_to_show | 8 | DM, MCS, MDS, EMHQS, MBA, EMBA, MDM, MTT |
| safe_with_warning | 7 | MGT, ACC, FIN, ECOM, CS, IT, DS |
| **hide_from_mobile_until_review** | 4 | **HCI, PH, MHA, ENGT** |

---

## 3. أخطر 10 فجوات

| # | البرنامج | الفجوة | الخطورة |
|---|----------|--------|---------|
| 1 | **PH** | الخطة مُمثَّلة كـ **4 سنوات** [1,2,3,4] لا 8 فصول؛ المجموع DB=**151** مقابل 133 رسمي (**+18**). برنامج عربي بنية سنوية. | قاتلة للعرض |
| 2 | **HCI** | **المستوى الثاني محذوف** رغم وجوده في المصدر («المستوى الثاني»)؛ 13 مقرر/38س مكدّسة في Level 8؛ المجموع **151** مقابل 130 (**+21**). | قاتلة (خطأ استخراج) |
| 3 | **MHA** | المجموع DB=**42** مقابل 36 رسمي صريح (**+6**). | عالية |
| 4 | **ENGT** | DB فيه Level 2 (6 مقررات/17س) بينما صفحة المصدر الرسمية «First Year → Level three» (لا Level 2). توزيع المقررات على المستويات غير متصالح. | عالية |
| 5 | **CS/IT/DS** | مجموع الخطة المعروض **أقل 12 ساعة** من الرسمي (الاختياريات بـ level=None). الموبايل سيعرض ساعات أقل من الحقيقة لو جمع الخطة. | متوسطة |
| 6 | **IT** | 12 عنصر اختياري placeholder (IT4XX) بـ level=None — الأكثر بين الثلاثة. | متوسطة |
| 7 | **MGT/ACC/FIN/ECOM** | فجوة First Year → Level three (لا Level 2). العرض الخام يُظهر «مستوى 1 ثم 3» مربكًا. | متوسطة (عرض فقط) |
| 8 | **كل الـ19** | `source_document/url/sha256` = NULL في `cat_programs`. لا provenance. | حوكمة |
| 9 | **CS مقابل IT/DS** | CS يقسم ENG001 على Level 1+2، بينما IT/DS يضعانه في Level 1 فقط. نمذجة السنة الأولى غير متسقة بين برامج شقيقة. | منخفضة |
| 10 | **كل الـ19** | `num_levels` = NULL. الموبايل يشتق عدد المستويات من الخطة (يكسر مع PH/HCI). | متوسطة |

---

## 4. الجدول الكامل لكل البرامج

> **مفتاح:** off = `total_credits_official` في DB. plan = مجموع ساعات الخطة المخزّنة (المستويات). reconcile = هل يتصالح المجموع مع الرسمي (مع احتساب قاعدة الاختياري عند وجودها).

| Program | Degree | Source file | Conf. | Off cr | Plan cr | Recon. | Source structure | DB levels | All levels present | Special courses | Gap | Mobile | Cert. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **MGT** | bachelor | study-plan-ba-dec-2025.pdf | official_pdf | 130 | 130 | ✅ | First Year → Level three…Eight | [1,3,4,5,6,7,8] | ✅ (FY=1) | ENG001 16س (جزآن) | لا Level 2 (رسمي) | warning | needs_semantics |
| **ACC** | bachelor | acc_accounting_plan.pdf | official_pdf | 130 | 130 | ✅ | First Year → Level three…Eight | [1,3,4,5,6,7,8] | ✅ | ENG001 16س | لا Level 2 (رسمي) | warning | needs_semantics |
| **FIN** | bachelor | fin_finance_plan.pdf | official_pdf | 130 | 130 | ✅ | First Year → Level three…Eight | [1,3,4,5,6,7,8] | ✅ | ENG001 16س | لا Level 2 (رسمي) | warning | needs_semantics |
| **ECOM** | bachelor | ecom_ecommerce_plan.pdf | official_pdf | 130 | 130 | ✅ | First Year → Level three…Eight | [1,3,4,5,6,7,8] | ✅ | ENG001 16س | لا Level 2 (رسمي) | warning | needs_semantics |
| **CS** | bachelor | bs-cs-plan-sep2023.docx | official_docx | 130 | 118 | ✅ (4 من 8 اختياري=12) | First Year → Level three…Eight | [1,2,3,4,5,6,7,8,None] | ✅ | ENG001 8+8؛ 8 اختياري (مساران) | اختياريات level=None | warning | needs_semantics |
| **IT** | bachelor | bs-it-plan-sep2023.pdf | official_pdf | 130 | 118 | ✅ (4 اختياري=12) | First Year → Level three…Eight | [1,3,4,5,6,7,8,None] | ✅ | 12 اختياري IT4XX | اختياريات level=None | warning | needs_semantics |
| **DS** | bachelor | (علوم البيانات).pdf | official_pdf | 133 | 121 | ✅ (4 من 8 اختياري=12) | First Year → Level three…Eight | [1,3,4,5,6,7,8,None] | ✅ | 8 اختياري (AI/BigData) | اختياريات level=None | warning | needs_semantics |
| **DM** | bachelor | dm_digital_media_plan.pdf | official_pdf | 125 | 125 | ✅ | **Level One → Level Eight** (كامل) | [1,2,3,4,5,6,7,8] | ✅ | — | لا فجوة | **safe** | **certified_100** |
| **ENGT** | bachelor | (اللغة الإنجليزية والترجمة).pdf | official_pdf | 127 | 127 | ✅ (مجموع) | First Year → Level three…Eight | [1,2,3,4,5,6,7,8] | ⚠️ Level 2 في DB لا في المصدر | ENG001 16س؛ Step test مذكور للقبول | **توزيع Level 2/3 غير متصالح** | hide | **needs_source** |
| **HCI** | bachelor | hci_health_informatics_plan_aug2025.pdf | official_pdf(AR) | 130 | **151** | ❌ +21 | «المستوى الأول/الثاني/الثالث…» | [1,3,4,5,6,7,8] | ❌ **Level 2 محذوف** | اختياري 6س | **حذف L2 + تكدّس L8 + +21** | **hide** | **needs_source** |
| **PH** | bachelor | ph_public_health_plan_aug2023.pdf | official_pdf(AR) | 133 | **151** | ❌ +18 | **سنوات**: السنة الأولى/الثانية → فصلان | [1,2,3,4]=4 سنوات | ❌ ليست فصولًا | نجل001 16س؛ سنة تحضيرية | **نموذج سنوي لا فصلي + +18** | **hide** | **needs_source** |
| **MBA** | master | mba-handbook-2025-ar.pdf | official_pdf(AR) | 36 | 36 | ✅ | فصول (4) | [1,2,3,4] | ✅ | فصل تأسيسي لغير تخصص الإدارة | لا فجوة | safe | **certified_100** |
| **EMBA** | exec_master | emba-plan-2025.docx | official_docx(AR) | 36 | 36 | ✅ | سنتان × فصلان (4) | [1,2,3,4] | ✅ | — | لا فجوة | safe | **certified_100** |
| **MDM** | master | mba-digital-marketing-plan.pdf | official_pdf(AR) | 36 | 36 | ✅ | سنتان × فصلان (4) | [1,2,3,4] | ✅ | — | لا فجوة | safe | **certified_100** |
| **EMHQS** | exec_master | emhqs_studyplan.pdf | official_pdf | 36 | 36 | ✅ | Semester 1–4 (incl capstone) | [1,2,3,4] | ✅ | Capstone project | لا فجوة | safe | **certified_100** |
| **MHA** | master | mha-study-plan-arabic.pdf | official_pdf(AR) | 36 | **42** | ❌ +6 | «المستوى الأول…الرابع» | [1,2,3,4] | ✅ بنية | تدريب ميداني/مشروع | **+6 ساعات زائدة** | **hide** | **needs_source** |
| **MCS** | master | mcs-study-plan-sep2023.pdf | official_pdf | 36 | 36 | ✅ | Level One → Level Four | [1,2,3,4] | ✅ | Capstone | لا فجوة | safe | **certified_100** |
| **MDS** | master | mds-study-plan-sep2023.pdf | official_pdf | 36 | 36 | ✅ | Level One → Level Four | [1,2,3,4] | ✅ | — | لا فجوة | safe | **certified_100** |
| **MTT** | master | mtt_translation-technologies-2021.pdf | official_pdf(AR) | 36 | 36 | ✅ | فصول (سنتان) | [1,2,3,4] | ✅ | — | لا فجوة (مصدر مقتضب) | safe | **certified_100** |

---

## 5. ما يُسمح للموبايل بعرضه الآن

**يُعرض مباشرة (safe_to_show) — 8 برامج:**
DM, MCS, MDS, EMHQS, MBA, EMBA, MDM, MTT.
بنيتها متطابقة 100% مع المصدر، ساعاتها تتصالح، ومستوياتها تُعرض كما هي.

**يُعرض بقاعدة عرض + تحذير (safe_with_warning) — 7 برامج:**
MGT, ACC, FIN, ECOM, CS, IT, DS.
شرط العرض:
1. **عرض «السنة الأولى المشتركة»** بدل «المستوى 1» (وعدم اختلاق Level 2).
2. **استخدام `total_credits_official`** للساعات الكلية، **لا مجموع الخطة** (لأن CS/IT/DS أقل بـ12).
3. **عرض الاختياريات كمجموعة اختيار** «اختر 4» لا كمقررات مفقودة.

---

## 6. ما يجب حجبه من الموبايل الآن

**hide_from_mobile_until_review — 4 برامج:**

- **HCI** — حذف المستوى الثاني (موجود رسميًا) + تكدّس 13 مقرر في Level 8 + مجموع 151 بدل 130. خطأ استخراج مؤكد.
- **PH** — مُمثَّل كـ4 سنوات لا 8 فصول + مجموع 151 بدل 133. نموذج بنيوي خاطئ للعرض.
- **MHA** — مجموع 42 بدل 36 (+6). تضخّم ساعات.
- **ENGT** — Level 2 في DB يناقض «First Year → Level three» في المصدر؛ توزيع المقررات على المستويات غير موثوق.

عرض أيٍّ منها الآن = أول طالب يرى خطة خاطئة. هذه هي النقطة القاتلة للثقة التي حذّرت منها.

---

## 7. أين نحتاج Patch

| الطبقة | المطلوب |
|--------|---------|
| **catalog data** | إعادة استخراج **HCI** (استعادة المستوى الثاني، تفكيك تكدّس Level 8، تصحيح 151→130). إعادة نمذجة **PH** (فصول أو وسمها كـبنية سنوية، تصحيح +18). تصحيح **MHA** (+6). مصالحة **ENGT** (Level 2 مقابل 3). |
| **catalog schema** | `level_type` / `academic_phase` (`common_first_year`)؛ `program_structure_type` (`semester`\|`year`)؛ تفعيل `num_levels`؛ تسجيل provenance (`source_url`/`sha`)؛ وسم خانات الاختياري بقاعدة الاختيار والمستوى. |
| **catalog API** | `/plan` يعرض المجموع الرسمي (لا مجموع الخطة)؛ يفصل مجموعات الاختياري؛ يكشف `level_type` و`structure_type`. |
| **mobile display** | قاعدة «السنة الأولى» **بعلَم صريح فقط**؛ الاختياريات كـ«اختر N من M»؛ استخدام الساعات الرسمية؛ **حجب** غير المعتمدة. |

---

## 8. هل «السنة الأولى المشتركة» نمط عام أم لبرامج محددة؟

**لبرامج محددة — ليست نمطًا عامًا.** تنطبق على **7 برامج بكالوريوس تتشارك السنة الأولى المشتركة/التحضيرية في الجامعة:**
- كلية العلوم الإدارية والمالية: **MGT, ACC, FIN, ECOM**
- كلية الحوسبة والمعلوماتية: **CS, IT, DS**

**لا تنطبق على:**
- **DM** — يستخدم «Level One/Two» صراحةً، 8 مستويات كاملة.
- **HCI, PH** — كلية صحية، بنية مختلفة (PH سنوية، HCI يستخدم «المستوى الثاني»).
- **الماجستير** — 4 فصول.
- **ENGT** — يستخدم «First Year» في المصدر لكن DB مختلف (يحتاج مصالحة، ليس نمطًا نظيفًا).

---

## 9. هل تكفي قاعدة عرض عامة؟

**لا — القاعدة العامة المبنية على الفجوة خطيرة.**

القاعدة المقترحة «level=1 + غياب level 2 + التالي=3 → السنة الأولى المشتركة» تعمل لـ MGT/ACC/FIN/ECOM/IT/DS، **لكنها تُخفي خطأ HCI** (الذي يَغيب منه level 2 لأن الاستخراج حذفه، لا لأن المصدر كذلك). تطبيقها على HCI = إخفاء خطأ بيانات وتضليل الطالب.

**القاعدة الصحيحة:** علَم صريح `academic_phase = 'common_first_year'` يُضاف **فقط للبرامج المعتمدة السبعة** بعد التصديق. الموبايل يعرض «السنة الأولى المشتركة» **عند وجود العلَم** — لا يستنتجها من الفجوة أبدًا. والبرامج غير المعتمدة (HCI/PH/MHA/ENGT) محجوبة أصلًا فلا تصل لقاعدة العرض.

ملاحظة دقيقة: CS يقسم السنة الأولى على Level 1+2 (جزآ ENG001)، بينما IT/DS يضعانها في Level 1. العلَم الصريح يحلّ هذا التباين دون منطق هشّ.

---

## 10. القرار

```
READY_FOR_MOBILE_PLAN_DISPLAY_RULES   (لـ 15 برنامجًا: 8 safe + 7 warning)
+
HIDE_LIST = { HCI, PH, MHA, ENGT }     (needs_source_review — محجوبة)
+
catalog data/schema patch track موازٍ للبرامج الأربعة المحجوبة
```

**التفسير:**
- **15 برنامجًا جاهزة للعرض** الآن: 8 مباشرة + 7 بقاعدة «السنة الأولى» (بعلَم صريح) + استخدام الساعات الرسمية + الاختياريات كاختيار.
- **4 برامج محجوبة** حتى patch الكتالوج (HCI/PH/MHA/ENGT) — أخطاؤها بيانية لا عرضية.
- **لا حاجة لحجب كل الموبايل** (BLOCK_… مرفوض — 12 برنامجًا نظيفة فعلًا).
- الخطوة التالية المنطقية: اعتماد `academic_phase` flag + hide-list في الموبايل (Wave التالي)، وبالتوازي patch بيانات الأربعة.

**ممنوع نُفِّذ في هذا الـ Audit:** لا DB writes، لا migration، لا commit، لا push، لا deploy، لا mobile changes. تشخيص فقط.

---

## ملحق أ: تفصيل مصالحة الساعات للحالات الحرجة

| Program | leveled cr | level=None (electives) | قاعدة الاختيار | الرسمي | المصالحة |
|---------|-----------|------------------------|----------------|--------|----------|
| CS | 118 | 8 مقررات / 24س | اختر 4 (=12) | 130 | 118+12 = 130 ✅ |
| IT | 118 | 12 مقرر / 36س | اختر 4 (=12) | 130 | 118+12 = 130 ✅ |
| DS | 121 | 8 مقررات / 24س | اختر 4 (=12) | 133 | 121+12 = 133 ✅ |
| HCI | 151 (مع Level 8 مكدّس) | — | — | 130 | **+21 لا يتصالح** ❌ |
| PH | 151 (4 سنوات) | — | — | 133 | **+18 لا يتصالح** ❌ |
| MHA | 42 | — | — | 36 | **+6 لا يتصالح** ❌ |

## ملحق ب: المصادر الرسمية المعتمدة لكل برنامج
كل المصادر من مستودع الجامعة `0-Universities/1- Saudi Electronic University/1. StudyPlans/` (بكالوريوس/دراسات عليا) و`_official_downloads/2026-06-21/`. التفاصيل في عمود "Source file" بالجدول الكامل. **تنبيه provenance:** لم يُسجَّل أيٌّ من هذه المسارات في `cat_programs.source_*` (كلها NULL) — يجب تسجيلها ضمن patch الـ schema.
