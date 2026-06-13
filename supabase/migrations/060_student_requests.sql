-- Migration 060: Student Request Operating System
--
-- request_templates  — one row per request type, seeded with 10 SEU request types
-- student_requests   — every request a student creates or drafts

-- ── request_templates ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS request_templates (
    request_type        TEXT        PRIMARY KEY,
    title_ar            TEXT        NOT NULL,
    description_ar      TEXT,
    target_entity       TEXT        NOT NULL
                            CHECK (target_entity IN (
                                'professor','department','college',
                                'registrar','financial','it_support','deanship'
                            )),
    required_fields     JSONB       NOT NULL DEFAULT '[]',
    deadline_rule       TEXT,
    deadline_days       INT,
    body_template_ar    TEXT        NOT NULL,
    attachments_needed  TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    escalation_days     INT         DEFAULT 7,
    active              BOOL        NOT NULL DEFAULT true
);

-- ── Seed: 10 most common SEU academic request types ──────────────────────────

INSERT INTO request_templates
    (request_type, title_ar, description_ar, target_entity,
     required_fields, deadline_rule, deadline_days,
     body_template_ar, attachments_needed, escalation_days, active)
VALUES

-- 1. exam_excuse
(
    'exam_excuse',
    'عذر غياب عن اختبار',
    'طلب قبول عذر الغياب عن اختبار منتصف الفصل أو النهائي',
    'professor',
    '[
      {"field_name":"course_code","question_ar":"ما رمز المقرر الذي غبت عن اختباره؟","field_type":"course_code","required":true},
      {"field_name":"exam_date","question_ar":"ما تاريخ الاختبار الذي غبت عنه؟","field_type":"date","required":true},
      {"field_name":"excuse_reason","question_ar":"ما سبب غيابك؟","field_type":"select","options":["مرض","وفاة قريب","ظرف طارئ","سفر اضطراري","سبب آخر"],"required":true},
      {"field_name":"proof_type","question_ar":"ما نوع المستند الذي تملكه؟","field_type":"select","options":["تقرير طبي","إفادة من المستشفى","وثيقة رسمية","لا يوجد مستند"],"required":true}
    ]'::JSONB,
    'خلال 3 أيام عمل من تاريخ الاختبار', 3,
    $tmpl$الأستاذ/ة الدكتور/ة {professor_name} المحترم/ة،

السلام عليكم ورحمة الله وبركاته،

أتقدم إليكم بهذا الطلب راجياً قبول عذري عن الاختبار المقرر لمادة {course_name} ({course_code}) بتاريخ {exam_date}.

سبب الغياب: {excuse_reason}.

وأتعهد بتقديم المستندات الداعمة فور الحصول عليها.

مقدمه،
{student_name}
{student_id}
{program_name}$tmpl$,
    ARRAY['تقرير طبي موثق أو وثيقة رسمية']::TEXT[],
    5, true
),

-- 2. grade_appeal
(
    'grade_appeal',
    'اعتراض على درجة',
    'طلب مراجعة الدرجة النهائية في مقرر',
    'professor',
    '[
      {"field_name":"course_code","question_ar":"ما رمز المقرر؟","field_type":"course_code","required":true},
      {"field_name":"semester","question_ar":"أي فصل دراسي؟","field_type":"text","required":true},
      {"field_name":"grade_received","question_ar":"ما الدرجة التي حصلت عليها؟","field_type":"text","required":true},
      {"field_name":"reason","question_ar":"ما سبب اعتراضك على الدرجة؟","field_type":"text","required":true}
    ]'::JSONB,
    'خلال أسبوع من إعلان النتائج', 7,
    $tmpl$الأستاذ/ة الدكتور/ة {professor_name} المحترم/ة،

تحية طيبة،

أرجو التكرم بمراجعة درجتي في مادة {course_name} ({course_code}) للفصل الدراسي {semester}، حيث حصلت على درجة {grade_received}.

سبب الاعتراض: {reason}.

أرجو النظر في طلبي، وأنا على أتم الاستعداد لأي إيضاحات إضافية.

مقدمه،
{student_name} — {student_id}$tmpl$,
    ARRAY['كشف الدرجات الرسمي']::TEXT[],
    5, true
),

-- 3. exam_postpone
(
    'exam_postpone',
    'طلب تأجيل اختبار',
    'طلب تأجيل موعد الاختبار لظرف طارئ',
    'department',
    '[
      {"field_name":"course_code","question_ar":"رمز المقرر؟","field_type":"course_code","required":true},
      {"field_name":"exam_date","question_ar":"تاريخ الاختبار الأصلي؟","field_type":"date","required":true},
      {"field_name":"reason","question_ar":"سبب طلب التأجيل؟","field_type":"text","required":true},
      {"field_name":"suggested_date","question_ar":"هل لديك تاريخ مقترح بديل؟","field_type":"date","required":false}
    ]'::JSONB,
    'قبل 48 ساعة من موعد الاختبار على الأقل', 0,
    $tmpl$رئيس/ة القسم المحترم/ة،

السلام عليكم،

أتقدم بطلب تأجيل اختبار مادة {course_name} ({course_code}) المقرر في {exam_date} للظروف الطارئة التالية: {reason}.

{suggested_date_text}

شاكراً حسن تعاونكم،
{student_name} — {student_id} — {program_name}$tmpl$,
    ARRAY['وثيقة إثبات الظرف الطارئ']::TEXT[],
    3, true
),

-- 4. course_drop_late
(
    'course_drop_late',
    'حذف مادة بعد الموعد النظامي',
    'طلب حذف مادة بعد انتهاء فترة الحذف والإضافة',
    'college',
    '[
      {"field_name":"course_code","question_ar":"رمز المادة المراد حذفها؟","field_type":"course_code","required":true},
      {"field_name":"reason","question_ar":"سبب طلب الحذف المتأخر؟","field_type":"text","required":true},
      {"field_name":"gpa_impact","question_ar":"هل أنت على دراية بأثر الحذف على معدلك؟","field_type":"select","options":["نعم","أحتاج توضيحاً"],"required":true}
    ]'::JSONB,
    'قبل الأسبوع الأخير من الفصل', NULL,
    $tmpl$عميد/ة الكلية المحترم/ة،

السلام عليكم،

أرجو الموافقة على حذف مادة {course_name} ({course_code}) من جدولي الدراسي للفصل الحالي.

السبب: {reason}.

أعي تماماً الأثر الأكاديمي لهذا الطلب وأتحمل المسؤولية الكاملة.

مقدمه،
{student_name} — {student_id}
البرنامج: {program_name}$tmpl$,
    ARRAY[]::TEXT[],
    5, true
),

-- 5. withdrawal
(
    'withdrawal',
    'انسحاب من الفصل الدراسي',
    'طلب الانسحاب الرسمي من الفصل الدراسي الحالي',
    'registrar',
    '[
      {"field_name":"reason","question_ar":"سبب الانسحاب من الفصل؟","field_type":"text","required":true},
      {"field_name":"return_semester","question_ar":"هل تنوي العودة؟ في أي فصل؟","field_type":"text","required":false}
    ]'::JSONB,
    'قبل نهاية الأسبوع العاشر من الفصل', NULL,
    $tmpl$مسجل الجامعة المحترم،

السلام عليكم،

أتقدم بطلب الانسحاب الرسمي من الفصل الدراسي الحالي {current_semester}.

السبب: {reason}.

{return_plan_text}

مع التقدير،
{student_name} — {student_id} — {program_name}$tmpl$,
    ARRAY['وثيقة داعمة إن وجدت']::TEXT[],
    3, true
),

-- 6. certificate_request
(
    'certificate_request',
    'طلب إفادة أو شهادة قيد',
    'طلب إفادة رسمية بالقيد أو شهادة لغرض معين',
    'registrar',
    '[
      {"field_name":"purpose","question_ar":"لماذا تحتاج الإفادة؟","field_type":"select","options":["جهة عمل","تدريب","بعثة","بنك","جهة حكومية","سفارة","أخرى"],"required":true},
      {"field_name":"language","question_ar":"اللغة المطلوبة؟","field_type":"select","options":["عربي","إنجليزي","كلاهما"],"required":true},
      {"field_name":"copies","question_ar":"كم نسخة تحتاج؟","field_type":"text","required":true}
    ]'::JSONB,
    'خلال 3 إلى 5 أيام عمل', NULL,
    $tmpl$قسم القبول والتسجيل المحترم،

أرجو إصدار إفادة رسمية بقيدي في الجامعة باللغة {language} لغرض {purpose} ({copies} نسخة).

المعلومات:
الاسم: {student_name}
الرقم الجامعي: {student_id}
البرنامج: {program_name}
الفصل الحالي: {current_semester}

شكراً،$tmpl$,
    ARRAY[]::TEXT[],
    3, true
),

-- 7. equivalency_request
(
    'equivalency_request',
    'طلب معادلة مقرر',
    'طلب معادلة مقرر مدروس في جامعة أخرى',
    'department',
    '[
      {"field_name":"external_course","question_ar":"اسم ورمز المقرر الخارجي؟","field_type":"text","required":true},
      {"field_name":"external_university","question_ar":"اسم الجامعة التي درست فيها؟","field_type":"text","required":true},
      {"field_name":"target_course","question_ar":"رمز المقرر الذي تريد معادلته في SEU؟","field_type":"course_code","required":true},
      {"field_name":"grade","question_ar":"الدرجة التي حصلت عليها في المقرر الخارجي؟","field_type":"text","required":true}
    ]'::JSONB,
    'في بداية الفصل الدراسي', NULL,
    $tmpl$رئيس/ة القسم المحترم/ة،

أتقدم بطلب معادلة مقرر {external_course} من جامعة {external_university} (درجة: {grade}) مع مقرر {target_course_name} ({target_course}) في برنامجنا.

مرفق: كشف الدرجات وتوصيف المقرر الخارجي.

مقدمه،
{student_name} — {student_id}$tmpl$,
    ARRAY['كشف الدرجات الرسمي المصدق','توصيف المقرر (Course Description)']::TEXT[],
    5, true
),

-- 8. graduation_request
(
    'graduation_request',
    'طلب تسجيل للتخرج',
    'إشعار الجامعة باكتمال متطلبات التخرج',
    'registrar',
    '[
      {"field_name":"expected_semester","question_ar":"الفصل المتوقع للتخرج؟","field_type":"text","required":true},
      {"field_name":"remaining_hours","question_ar":"كم ساعة معتمدة تبقى عليك؟","field_type":"text","required":true}
    ]'::JSONB,
    'الفصل قبل الأخير على الأقل', NULL,
    $tmpl$قسم القبول والتسجيل المحترم،

أود إشعار الجامعة بقرب استيفائي لمتطلبات التخرج من برنامج {program_name}.

الفصل المتوقع للتخرج: {expected_semester}.
الساعات المعتمدة المتبقية: {remaining_hours} ساعة.

أرجو تزويدي بأي متطلبات إضافية.

{student_name} — {student_id}$tmpl$,
    ARRAY['كشف الدرجات غير الرسمي']::TEXT[],
    3, true
),

-- 9. grade_review
(
    'grade_review',
    'طلب مراجعة ورقة الإجابة',
    'طلب الاطلاع على ورقة الإجابة بعد الاختبار',
    'professor',
    '[
      {"field_name":"course_code","question_ar":"رمز المقرر؟","field_type":"course_code","required":true},
      {"field_name":"exam_type","question_ar":"نوع الاختبار؟","field_type":"select","options":["منتصف الفصل","النهائي","قصير"],"required":true},
      {"field_name":"specific_question","question_ar":"هل هناك سؤال محدد تريد مراجعته؟","field_type":"text","required":false}
    ]'::JSONB,
    'خلال أسبوع من إعلان الدرجات', 7,
    $tmpl$الأستاذ/ة الدكتور/ة {professor_name} المحترم/ة،

أرجو التكرم بالسماح لي بمراجعة ورقة إجابتي في اختبار {exam_type} لمادة {course_name} ({course_code}).

{specific_question_text}

شاكراً تعاونكم،
{student_name} — {student_id}$tmpl$,
    ARRAY[]::TEXT[],
    3, true
),

-- 10. blackboard_issue
(
    'blackboard_issue',
    'مشكلة في بلاك بورد',
    'الإبلاغ عن مشكلة تقنية في منصة بلاك بورد',
    'it_support',
    '[
      {"field_name":"issue_type","question_ar":"نوع المشكلة؟","field_type":"select","options":["لا أستطيع الدخول","لا أرى المقرر","لا أستطيع رفع ملف","الاختبار لم يُسجَّل","مشكلة في المحتوى","أخرى"],"required":true},
      {"field_name":"course_code","question_ar":"هل المشكلة في مقرر محدد؟","field_type":"course_code","required":false},
      {"field_name":"since_when","question_ar":"منذ متى وأنت تواجه هذه المشكلة؟","field_type":"text","required":true},
      {"field_name":"tried","question_ar":"ماذا جربت لحل المشكلة؟","field_type":"text","required":false}
    ]'::JSONB,
    NULL, NULL,
    $tmpl$الدعم التقني المحترم،

أواجه مشكلة تقنية في منصة بلاك بورد: {issue_type}.

{course_ref_text}
المشكلة بدأت منذ: {since_when}.
{tried_text}

أرجو المساعدة في أقرب وقت.
{student_name} — {student_id}$tmpl$,
    ARRAY['لقطة شاشة للمشكلة إن أمكن']::TEXT[],
    2, true
)

ON CONFLICT (request_type) DO UPDATE SET
    title_ar           = EXCLUDED.title_ar,
    description_ar     = EXCLUDED.description_ar,
    target_entity      = EXCLUDED.target_entity,
    required_fields    = EXCLUDED.required_fields,
    deadline_rule      = EXCLUDED.deadline_rule,
    deadline_days      = EXCLUDED.deadline_days,
    body_template_ar   = EXCLUDED.body_template_ar,
    attachments_needed = EXCLUDED.attachments_needed,
    escalation_days    = EXCLUDED.escalation_days;


-- ── student_requests ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS student_requests (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id          UUID        NOT NULL REFERENCES rumman_users(id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL,

    request_type        TEXT        NOT NULL REFERENCES request_templates(request_type),
    title               TEXT        NOT NULL,

    status              TEXT        NOT NULL DEFAULT 'draft'
                            CHECK (status IN (
                                'draft','ready','submitted',
                                'pending','resolved','rejected','escalated','cancelled'
                            )),

    collected_fields    JSONB       NOT NULL DEFAULT '{}',
    body_draft          TEXT,
    body_final          TEXT,

    target_entity       TEXT,
    target_name         TEXT,

    deadline_at         TIMESTAMPTZ,
    follow_up_at        TIMESTAMPTZ,
    submitted_at        TIMESTAMPTZ,
    resolved_at         TIMESTAMPTZ,

    conversation        JSONB       NOT NULL DEFAULT '[]',

    attachments_needed  TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    attachments_provided TEXT[]     NOT NULL DEFAULT ARRAY[]::TEXT[],

    task_id             UUID,
    course_code         TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sr_student_status
    ON student_requests (student_id, status, created_at DESC);

CREATE INDEX idx_sr_follow_up
    ON student_requests (follow_up_at)
    WHERE status IN ('submitted','pending') AND follow_up_at IS NOT NULL;


-- ── RPC: start_student_request ───────────────────────────────────────────────

CREATE OR REPLACE FUNCTION start_student_request(
    p_student_id   UUID,
    p_tenant_id    UUID,
    p_request_type TEXT,
    p_course_code  TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql AS $$
DECLARE
    v_template  request_templates%ROWTYPE;
    v_req_id    UUID;
    v_first_q   JSONB;
    v_deadline  TIMESTAMPTZ;
BEGIN
    SELECT * INTO v_template FROM request_templates
    WHERE request_type = p_request_type AND active = true;

    IF NOT FOUND THEN
        RETURN json_build_object('error', 'Unknown request type: ' || p_request_type);
    END IF;

    IF v_template.deadline_days IS NOT NULL THEN
        v_deadline := now() + (v_template.deadline_days || ' days')::INTERVAL;
    END IF;

    INSERT INTO student_requests (
        student_id, tenant_id, request_type, title,
        status, target_entity, course_code,
        deadline_at, follow_up_at, attachments_needed,
        conversation
    ) VALUES (
        p_student_id, p_tenant_id, p_request_type, v_template.title_ar,
        'draft', v_template.target_entity, p_course_code,
        v_deadline,
        CASE WHEN v_deadline IS NOT NULL
             THEN v_deadline - INTERVAL '1 day' ELSE NULL END,
        v_template.attachments_needed,
        jsonb_build_array(jsonb_build_object(
            'role', 'assistant',
            'content', 'فهمت — هذا طلب ' || v_template.title_ar || '. سأساعدك في إعداده.',
            'ts', now()::TEXT
        ))
    )
    RETURNING id INTO v_req_id;

    SELECT rf INTO v_first_q
    FROM   jsonb_array_elements(v_template.required_fields) rf
    WHERE  (rf->>'required')::bool = true
    LIMIT  1;

    RETURN json_build_object(
        'request_id',     v_req_id,
        'request_type',   p_request_type,
        'title',          v_template.title_ar,
        'deadline',       v_deadline,
        'attachments',    v_template.attachments_needed,
        'first_question', v_first_q
    );
END;
$$;
