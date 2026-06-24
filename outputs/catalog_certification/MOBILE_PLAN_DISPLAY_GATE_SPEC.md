# Mobile Plan Display Gate — Spec (React Native, executable)

**التاريخ:** 2026-06-24
**المرجع:** `CATALOG_CERTIFICATION_DECISION_MATRIX.json` + `FULL_CATALOG_PLAN_CERTIFICATION_AUDIT.md`
**المبدأ:** البرنامج يظهر في الأونبوردينج دائمًا (لا نُخفي برنامجًا حقيقيًا للجامعة). **الخطة التفصيلية** هي ما يُحجب أو يُوسم. لا يصل أي طالب لخطة فيها ساعات غلط أو مستوى مفقود.

---

## 0. مصدر القرار (gate source of truth)

الموبايل **لا يحسب** حالة الاعتماد. يقرأها لكل برنامج من حقل واحد:

```
program.certification = {
  status: "certified_100" | "safe_with_semantics" | "hide_plan_until_patch" | "blocked",
  plan_display: "show_full_plan" | "show_with_semantics" | "show_program_only_hide_plan" | "hide_program",
  academic_phase: "common_first_year" | "standard" | "year_based",
  semantics: { english_foundation, elective_pool, step_exemption_possible, auto_scheduled_first_year, ... },
  smart_registration: "yes_levels_3_plus" | "out_of_scope_v1_masters" | "no"
}
```

**التوصية:** هذا الحقل يُخدَم من **API** (مشتق من سجل اعتماد على الخادم)، لا من ثابت في الموبايل — حتى لا تحتاج إعادة نشر التطبيق عند اعتماد برنامج. **حل v1 المؤقت المقبول:** ثابت `CERTIFICATION` في الموبايل (مذكور في القسم 5) لحين جهوزية حقل API، على أن يُستبدل فورًا.

---

## A. ماذا يظهر في Onboarding لكل حالة

كل البرامج الـ19 تظهر كبطاقات قابلة للاختيار. الفرق في ما بعد الاختيار:

| الحالة | البطاقة في قائمة البرامج | بعد الاختيار |
|--------|--------------------------|--------------|
| **certified_100** | تظهر عادية | يكمل الأونبوردينج + خطة كاملة |
| **safe_with_semantics** | تظهر عادية | يكمل الأونبوردينج + خطة بطبقة دلالات (سنة أولى مشتركة/اختياريات) |
| **hide_plan_until_patch** | تظهر عادية (بلا أي وسم سلبي في القائمة) | يكمل الأونبوردينج (هوية + مستوى) لكن **شاشة الخطة تُستبدل بوسم «قيد التحقق»** |
| **blocked** (لا يوجد حاليًا) | لا تظهر | — |

**قاعدة:** في الأونبوردينج لا نطلب من الطالب «المستوى» كرقم خام للبرامج ذات `academic_phase = common_first_year`؛ نعرض **«السنة الأولى المشتركة»** كخيار أول ثم المستويات 3..8.

نص عند اختيار برنامج محجوب الخطة (شاشة تأكيد الاختيار، اختياري):
> «تم تسجيل برنامجك. خريطة الخطة التفصيلية قيد التحقق وسنفعّلها قريبًا — تقدر تكمل إعداد ملفك الآن.»

---

## B. ماذا يظهر في شاشة خريطة التقدم (النصوص العربية حرفيًا)

### B1. السنة الأولى المشتركة (common_first_year)
بدل «المستوى 1»:
> **السنة الأولى المشتركة**

نص فرعي (اختياري) أسفلها:
> «سنة تأسيسية مشتركة قبل مستويات التخصص.»

ولا يُعرض «المستوى 2» مطلقًا لهذه البرامج (الانتقال من السنة الأولى إلى المستوى الثالث رسمي).

### B2. برنامج محجوب الخطة (hide_plan_until_patch)
بدل خريطة المستويات:
> **«خطة هذا البرنامج قيد التحقق قبل عرض الخريطة التفصيلية. يمكنك متابعة إعداد ملفك الآن، وسنحدّث الخطة عند اعتمادها.»**

تظهر معها هوية البرنامج (الاسم، الكلية، الساعات الرسمية إن وُجدت) لكن **لا تُعرض قائمة مستويات/مقررات**.

### B3. الاختيارات الاختيارية (elective_pool / level=None)
عنوان المجموعة:
> **متطلبات اختيارية — اختر منها حسب الخطة الرسمية**

نص فرعي عند معرفة عدد الاختيار:
> «اختر {n} مقررات من القائمة.» (مثال CS/DS: اختر 4 من 8)

**لا تُعرض هذه المقررات داخل مستوى رقمي**، بل ككتلة «اختيارية» منفصلة. ولا تُحتسب كنواقص في شريط التقدم.

### B4. الساعات الكلية
يُعرض **`official_total_credits`** دائمًا (مثلاً CS = 130)، **لا مجموع الخطة** (118). شريط التقدم يستخدم الرقم الرسمي مقامًا.

### B5. مقررات اللغة الإنجليزية التأسيسية (english_foundation)
عند ENG001 (16 ساعة، جزآن):
> **«ENG001 — اللغة الإنجليزية (سنة تأسيسية، {credits} ساعة)»**

نص دلالة (إن وُجد علَم STEP):
> «قد يُعفى منها وفق نتيجة اختبار اللغة المعتمد — راجع القبول.»
(لا يُعرض أي رقم STEP محدد في الموبايل قبل اعتماد المصدر الرسمي للرقم.)

### B6. التقدير الذاتي (موجود من Wave 1)
> **«تقدير بناءً على اختياراتك، ولا يمثل سجلك الأكاديمي الرسمي.»**

---

## C. ما البرامج التي تظهر في الأونبوردينج

**كل الـ19 تظهر.** لا نُخفي برنامجًا. القاعدة: «أظهر البرنامج، اضبط الخطة».
- 15 برنامجًا: أونبوردينج كامل + خطة (8 كاملة + 7 بدلالات).
- 4 برامج (HCI, PH, MHA, ENGT): أونبوردينج كامل (هوية ومستوى) + خطة موسومة «قيد التحقق».
- إخفاء البرنامج كليًا (`hide_program`) محجوز لحالة `blocked` فقط — **لا يوجد منها حاليًا**.

**لماذا؟** طالب صحة عامة (PH) لا يجد برنامجه = فقدان ثقة فوري أسوأ من «الخطة قيد التحقق». إظهار البرنامج مع تأجيل الخطة يحفظ الثقة ويسمح ببناء الهوية.

---

## D. ما البرامج التي لا يجوز استخدامها في Smart Registration

Smart Registration (باني الجدول للمستويات 3..8) **يُمنع** لـ:

1. **المستوى 1 و2 / السنة الأولى المشتركة** — لكل البرامج (قاعدة منتج ثابتة).
2. **البرامج غير المعتمدة** — أي ليست `certified_100` أو `safe_with_semantics`.
3. **البرامج محجوبة الخطة** — HCI, PH, MHA, ENGT.
4. **أي برنامج needs source review** — نفس الأربعة.
5. **برامج الماجستير** — خارج نطاق v1 (Smart Registration مصمم لتدفق البكالوريوس ذي الـ8 مستويات): `out_of_scope_v1_masters`.

**المؤهّل لـ Smart Registration v1:** البكالوريوس المعتمد/الدلالي عند المستويات ≥3 فقط:
`MGT, ACC, FIN, ECOM, CS, IT, DS, DM` — وحصريًا من المستوى 3 فأعلى.
**ملاحظة DM:** يستخدم Level 1/2 حقيقيين؛ يُستثنى مستواه 1 و2 من Smart Registration بنفس القاعدة.

---

## Gate logic (pseudocode قابل للتنفيذ)

```js
// derive from program.certification (served by API; fallback to local CERTIFICATION map for v1)
function planView(program, declaredLevel) {
  const c = program.certification;
  if (c.plan_display === "hide_program") return { kind: "HIDDEN_PROGRAM" };
  if (c.plan_display === "show_program_only_hide_plan")
    return { kind: "PLAN_PENDING", text: T.planPending };           // B2
  // show_full_plan | show_with_semantics
  const view = { kind: "PLAN", totalCredits: program.official_total_credits };
  if (c.academic_phase === "common_first_year") view.firstYearLabel = T.commonFirstYear; // B1
  if (c.semantics.elective_pool) view.electiveBlock = T.electivePool;  // B3
  return view;
}

function canSmartRegister(program, level) {
  const c = program.certification;
  if (c.smart_registration === "no" || c.smart_registration === "out_of_scope_v1_masters") return false;
  if (level <= 2) return false;                 // common first year never
  return true;                                  // certified/semantics bachelor, level >= 3
}

const T = {
  commonFirstYear: "السنة الأولى المشتركة",
  planPending: "خطة هذا البرنامج قيد التحقق قبل عرض الخريطة التفصيلية. يمكنك متابعة إعداد ملفك الآن، وسنحدّث الخطة عند اعتمادها.",
  electivePool: "متطلبات اختيارية — اختر منها حسب الخطة الرسمية",
  selfEstimate: "تقدير بناءً على اختياراتك، ولا يمثل سجلك الأكاديمي الرسمي.",
};
```

---

## مصفوفة سريعة (gate × program)

| Program | onboarding | plan map | smart reg | عرض الخطة |
|---|---|---|---|---|
| DM, MCS, MDS, EMHQS, MBA, EMBA, MDM, MTT | ✅ | ✅ كاملة | (ماجستير خارج v1 / DM ≥3) | show_full_plan |
| MGT, ACC, FIN, ECOM | ✅ | ✅ بدلالات | ✅ ≥3 | السنة الأولى المشتركة + ساعات رسمية |
| CS, IT, DS | ✅ | ✅ بدلالات | ✅ ≥3 | + اختياريات كمجموعة + ساعات رسمية |
| **HCI, PH, MHA, ENGT** | ✅ | ❌ «قيد التحقق» | ❌ | show_program_only_hide_plan |
