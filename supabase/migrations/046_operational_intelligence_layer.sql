-- ============================================================
-- Migration: 046_operational_intelligence_layer.sql
-- Date:      2026-06-12
-- Author:    RUMMAN Platform
--
-- Purpose:
--   Adds the Operational Intelligence Layer — a set of tables,
--   views, column additions, and seed data that power the
--   RUMMAN Cockpit's pipeline observability, community Q&A
--   knowledge base, and real-time academic-calendar awareness.
--
-- Sections:
--   A. pipeline_runs      — audit log for every AI worker run
--   B. community_qa       — crowd-sourced + official Q&A bank
--   C. current_academic_context VIEW — live calendar phase
--   D. ALTER TABLE additions — fingerprint / pipeline linkage
--   E. Seed data          — 20 draft community_qa entries (Arabic)
--
-- Safety:
--   100% additive — no DROP, no DELETE, no ALTER COLUMN,
--   no TRUNCATE anywhere in this file.
-- ============================================================


-- ── A. pipeline_runs ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    worker_name      TEXT        NOT NULL,
    worker_version   TEXT,
    model_id         TEXT,
    prompt_hash      TEXT,
    openai_project   TEXT,
    started_at       TIMESTAMPTZ DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    records_input    INT         DEFAULT 0,
    records_output   INT         DEFAULT 0,
    tokens_used      INT         DEFAULT 0,
    cost_usd         NUMERIC(10, 4),
    status           TEXT        DEFAULT 'running'
                                 CHECK (status IN ('running', 'completed', 'failed', 'cancelled'))
);

COMMENT ON TABLE pipeline_runs IS
    'Audit log for every AI worker / pipeline execution. One row per run.';

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_worker_started
    ON pipeline_runs (worker_name, started_at);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_non_completed_status
    ON pipeline_runs (status)
    WHERE status != 'completed';


-- ── B. community_qa ──────────────────────────────────────────

-- pgvector extension must already be enabled; guard with IF NOT EXISTS
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS community_qa (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',

    canonical_question   TEXT        NOT NULL,
    question_aliases     TEXT[],
    question_embedding   VECTOR(1536),

    intent_category      TEXT        NOT NULL
                                     CHECK (intent_category IN (
                                         'registration',
                                         'attendance_excuse',
                                         'grade_query',
                                         'payment',
                                         'blackboard_technical',
                                         'schedule',
                                         'exam_procedure',
                                         'general_admin'
                                     )),

    college_code         TEXT,                   -- NULL = applies to all colleges
    course_code          TEXT,                   -- NULL = not course-specific

    answer_text          TEXT        NOT NULL,
    answer_summary       TEXT,                   -- one-line summary for Cockpit cards

    lifecycle_status     TEXT        NOT NULL DEFAULT 'draft'
                                     CHECK (lifecycle_status IN (
                                         'draft', 'active', 'needs_review', 'deprecated'
                                     )),

    source_type          TEXT        NOT NULL DEFAULT 'community'
                                     CHECK (source_type IN (
                                         'official', 'semi_official', 'community', 'unverified'
                                     )),

    confidence           NUMERIC(4, 3) DEFAULT 0.5
                                     CHECK (confidence BETWEEN 0 AND 1),

    verified_by          TEXT,
    source_doc_ids       UUID[],
    source_message_ids   UUID[],

    semester_scope       TEXT        DEFAULT 'always'
                                     CHECK (semester_scope IN (
                                         'always',
                                         'exam_period',
                                         'registration_window',
                                         'grade_release_period',
                                         'semester_start'
                                     )),

    valid_from           TIMESTAMPTZ,
    valid_until          TIMESTAMPTZ,

    confirmation_count   INT         DEFAULT 1,
    superseded_by        UUID        REFERENCES community_qa (id),

    needs_official_review BOOLEAN    DEFAULT FALSE,

    last_confirmed_at    TIMESTAMPTZ DEFAULT now(),
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE community_qa IS
    'Community-sourced and officially verified Q&A knowledge base for RUMMAN Cockpit. '
    'All entries start as draft and must be manually promoted to active.';

CREATE INDEX IF NOT EXISTS idx_cqa_intent_category
    ON community_qa (intent_category);

CREATE INDEX IF NOT EXISTS idx_cqa_college_code
    ON community_qa (college_code)
    WHERE college_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cqa_lifecycle_status
    ON community_qa (lifecycle_status);

CREATE INDEX IF NOT EXISTS idx_cqa_needs_official_review
    ON community_qa (needs_official_review)
    WHERE needs_official_review = TRUE;


-- ── C. current_academic_context VIEW ─────────────────────────

CREATE OR REPLACE VIEW current_academic_context AS
WITH

-- All events whose window contains today
active_events AS (
    SELECT
        tenant_id,
        academic_year,
        semester,
        event_type,
        event_name_ar,
        start_date,
        end_date
    FROM academic_calendar
    WHERE CURRENT_DATE BETWEEN start_date AND COALESCE(end_date, start_date)
),

-- Next 3 future events (starting strictly after today)
upcoming_raw AS (
    SELECT
        tenant_id,
        event_type,
        event_name_ar,
        start_date,
        (start_date - CURRENT_DATE)::INT AS days_away,
        ROW_NUMBER() OVER (
            PARTITION BY tenant_id
            ORDER BY start_date
        ) AS rn
    FROM academic_calendar
    WHERE start_date > CURRENT_DATE
),

upcoming_events AS (
    SELECT
        tenant_id,
        json_agg(
            json_build_object(
                'event_type',   event_type,
                'event_name_ar', event_name_ar,
                'start_date',   start_date,
                'days_away',    days_away
            )
            ORDER BY start_date
        ) AS upcoming_json
    FROM upcoming_raw
    WHERE rn <= 3
    GROUP BY tenant_id
),

-- Detect exam-proximity for pre_exam phase (within 14 days)
upcoming_exams AS (
    SELECT DISTINCT tenant_id
    FROM academic_calendar
    WHERE event_type IN ('final_exam', 'midterm_exam', 'final_exam_firstyear', 'midterm_exam_firstyear')
      AND start_date > CURRENT_DATE
      AND start_date <= CURRENT_DATE + INTERVAL '14 days'
),

-- Most recent semester_start that has already passed → identifies current semester
current_semester AS (
    SELECT DISTINCT ON (tenant_id)
        tenant_id,
        academic_year || '-' || semester AS semester_id
    FROM academic_calendar
    WHERE event_type = 'semester_start'
      AND start_date <= CURRENT_DATE
    ORDER BY tenant_id, start_date DESC
),

-- Aggregate active event types and Arabic labels per tenant
active_agg AS (
    SELECT
        tenant_id,
        array_agg(DISTINCT event_type)    AS active_windows,
        array_agg(DISTINCT event_name_ar) AS active_labels_ar
    FROM active_events
    GROUP BY tenant_id
)

SELECT
    aa.tenant_id,

    aa.active_windows,
    aa.active_labels_ar,

    COALESCE(ue.upcoming_json, '[]'::json) AS upcoming_events,

    -- academic_phase computation (priority order)
    CASE
        WHEN aa.active_windows && ARRAY[
            'final_exam', 'final_exam_firstyear',
            'midterm_exam', 'midterm_exam_firstyear'
        ]::TEXT[]
            THEN 'exam'

        WHEN ux.tenant_id IS NOT NULL
            THEN 'pre_exam'

        WHEN aa.active_windows && ARRAY[
            'course_registration', 'early_registration'
        ]::TEXT[]
            THEN 'registration'

        WHEN aa.active_windows @> ARRAY['exam_appeal_deadline']::TEXT[]
         AND NOT (aa.active_windows && ARRAY[
                'final_exam', 'final_exam_firstyear',
                'midterm_exam', 'midterm_exam_firstyear'
             ]::TEXT[])
            THEN 'grade_release'

        WHEN aa.active_windows && ARRAY[
            'holiday_eid_fitr', 'holiday_eid_adha'
        ]::TEXT[]
            THEN 'break'

        ELSE 'regular'
    END AS academic_phase,

    COALESCE(cs.semester_id, 'unknown') AS semester_id

FROM active_agg aa
LEFT JOIN upcoming_events ue ON ue.tenant_id = aa.tenant_id
LEFT JOIN upcoming_exams   ux ON ux.tenant_id = aa.tenant_id
LEFT JOIN current_semester cs ON cs.tenant_id = aa.tenant_id;

COMMENT ON VIEW current_academic_context IS
    'Real-time academic phase derived from the academic_calendar table. '
    'Returns one row per tenant with active windows, upcoming events (next 3), '
    'computed phase label, and current semester identifier.';


-- ── D. ALTER TABLE additions ──────────────────────────────────

-- exam_questions: question fingerprint (dedup) + pipeline linkage
ALTER TABLE exam_questions
    ADD COLUMN IF NOT EXISTS question_fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS pipeline_run_id      UUID
        REFERENCES pipeline_runs (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_eq_fingerprint
    ON exam_questions (question_fingerprint)
    WHERE question_fingerprint IS NOT NULL;

-- document_chunks: pipeline linkage
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS pipeline_run_id UUID
        REFERENCES pipeline_runs (id) ON DELETE SET NULL;

-- processing_jobs: Telegram file_unique_id for deduplication
ALTER TABLE processing_jobs
    ADD COLUMN IF NOT EXISTS file_unique_id TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_file_unique_id
    ON processing_jobs (file_unique_id)
    WHERE file_unique_id IS NOT NULL;


-- ── E. SEED — 20 draft community_qa entries (Arabic) ─────────
--
-- Rules enforced:
--   • lifecycle_status = 'draft'  (all — none promoted until reviewed)
--   • confidence ≤ 0.70 for community/semi_official
--   • confidence up to 0.95 for source_type = 'official'
--   • needs_official_review = TRUE for dates, deadlines, procedures

INSERT INTO community_qa (
    canonical_question,
    question_aliases,
    intent_category,
    answer_text,
    answer_summary,
    lifecycle_status,
    source_type,
    confidence,
    semester_scope,
    needs_official_review
) VALUES

-- 1. registration — opening date
(
    'متى يفتح تسجيل المواد؟',
    ARRAY['متى يبدأ التسجيل', 'موعد التسجيل'],
    'registration',
    'يُعلن عن موعد تسجيل المواد عبر البوابة الرسمية. عادةً يفتح في الأسبوع الأخير من الفصل السابق. تابع البوابة الأكاديمية وإشعاراتها.',
    'يعتمد على إعلان رسمي — تابع البوابة',
    'draft',
    'semi_official',
    0.70,
    'semester_start',
    TRUE
),

-- 2. registration — closing date
(
    'متى يقفل تسجيل المواد؟',
    ARRAY['آخر يوم تسجيل', 'موعد إغلاق التسجيل'],
    'registration',
    'تسجيل المواد عادةً يُغلق خلال الأسبوع الأول من بداية الدراسة. التاريخ الدقيق يظهر في البوابة الأكاديمية.',
    'خلال الأسبوع الأول — تحقق من البوابة',
    'draft',
    'semi_official',
    0.70,
    'registration_window',
    TRUE
),

-- 3. attendance_excuse — how to submit
(
    'كيف أرفع عذر غياب؟',
    ARRAY['طريقة رفع العذر', 'كيف أسوي عذر', 'خطوات رفع الغياب'],
    'attendance_excuse',
    'رفع عذر الغياب يتم عبر النظام الأكاديمي (البوابة). ادخل على الطلبات والخدمات ثم رفع عذر. ارفع المستند الداعم (تقرير طبي أو ما يثبت العذر). المدة المسموحة عادةً أسبوع من تاريخ الغياب.',
    'عبر البوابة → طلبات → رفع عذر',
    'draft',
    'semi_official',
    0.70,
    'always',
    FALSE
),

-- 4. attendance_excuse — government hospital certificate
(
    'هل يقبلون العذر الطبي من مستشفى حكومي؟',
    ARRAY['عذر طبي حكومي', 'مركز صحي حكومي'],
    'attendance_excuse',
    'تجربة الطلاب تشير إلى أن الأعذار من المستشفيات الحكومية مقبولة بشكل عام. المراكز الصحية الأولية الحكومية أيضاً مقبولة. الخاصة قد تحتاج توثيقاً إضافياً. الموافقة النهائية للدكتور.',
    'حكومي مقبول عادةً — الموافقة للدكتور',
    'draft',
    'community',
    0.60,
    'always',
    TRUE
),

-- 5. grade_query — when do grades appear
(
    'متى تنزل الدرجات؟',
    ARRAY['موعد ظهور النتائج', 'متى تظهر الدرجات', 'متى تصدر النتائج'],
    'grade_query',
    'الدرجات تظهر عبر البوابة عادةً خلال أسبوعين من انتهاء الاختبارات النهائية. التوقيت يختلف بين المقررات والدكاترة. لا يوجد موعد موحد معلن.',
    'خلال أسبوعين من الاختبارات — يختلف حسب المقرر',
    'draft',
    'community',
    0.65,
    'grade_release_period',
    TRUE
),

-- 6. grade_query — honor roll threshold (official)
(
    'كم أقل درجة لمرتبة الشرف الأولى؟',
    ARRAY['درجة مرتبة الشرف', 'شرط التميز', 'نسبة الشرف'],
    'grade_query',
    'وفق لوائح جامعة سعود الإلكترونية: مرتبة الشرف الأولى تتطلب معدلاً تراكمياً 3.75 فأعلى من 4.0. مرتبة الشرف الثانية: من 3.5 إلى أقل من 3.75.',
    'الأولى: 3.75+ | الثانية: 3.5 إلى 3.74',
    'draft',
    'official',
    0.95,
    'always',
    FALSE
),

-- 7. payment — how to pay tuition
(
    'كيف أسدد الرسوم الدراسية؟',
    ARRAY['طريقة الدفع', 'السداد الإلكتروني', 'وين أسدد'],
    'payment',
    'السداد يتم عبر: 1) البوابة الأكاديمية مباشرة ببطاقة الائتمان أو مدى، 2) تحويل بنكي على حساب الجامعة، 3) بعض فروع البنوك المعتمدة. بعد السداد قد يأخذ النظام 24 إلى 48 ساعة للتحديث.',
    'بوابة أو تحويل بنكي — يتأخر ظهوره 24-48 ساعة',
    'draft',
    'semi_official',
    0.70,
    'always',
    FALSE
),

-- 8. blackboard_technical — Blackboard not loading
(
    'وش أسوي إذا تعطل البلاك بورد؟',
    ARRAY['مشكلة بلاك بورد', 'البلاك بورد ما يفتح', 'خطأ في البلاك بورد'],
    'blackboard_technical',
    'الخطوات: 1) امسح cache المتصفح وملفات تعريف الارتباط، 2) جرب متصفح Chrome أو Edge محدّث، 3) إذا استمر المشكلة تواصل مع الدعم التقني للجامعة، 4) إذا كانت المشكلة قبل اختبار أخبر الدكتور فوراً وخذ screenshot للخطأ كدليل.',
    'امسح الكاش، جرب Chrome، ثم بلّغ الدكتور فوراً',
    'draft',
    'community',
    0.70,
    'always',
    FALSE
),

-- 9. exam_procedure — postponement request
(
    'كيف أطلب تأجيل الاختبار؟',
    ARRAY['طلب تأجيل', 'عذر اختبار', 'تغيب عن الاختبار'],
    'exam_procedure',
    'طلب تأجيل الاختبار: 1) قدّم الطلب عبر البوابة مع المستند الداعم خلال 3 أيام من تاريخ الاختبار، 2) القرار يعود لمنسق المقرر أو إدارة الكلية، 3) الاختبار البديل عادةً في نهاية الفصل الدراسي.',
    'عبر البوابة خلال 3 أيام — قرار الكلية',
    'draft',
    'semi_official',
    0.70,
    'exam_period',
    TRUE
),

-- 10. registration — drop/add a course
(
    'هل أقدر أحذف مادة؟',
    ARRAY['كيف أحذف مادة', 'موعد الحذف والإضافة', 'طلب حذف مقرر'],
    'registration',
    'الحذف والإضافة بدون أثر على السجل يكون في الأسبوع الأول فقط من بدء الدراسة. بعده يمكن الانسحاب برمز W في نافذة الانسحاب وسط الفصل لكنه يظهر في السجل الأكاديمي.',
    'الأسبوع الأول فقط للحذف بدون أثر',
    'draft',
    'semi_official',
    0.70,
    'registration_window',
    FALSE
),

-- 11. general_admin — open a support ticket
(
    'وين أفتح تذكرة دعم؟',
    ARRAY['كيف أتواصل مع الدعم', 'رابط الدعم الفني', 'طريقة فتح تذكرة'],
    'general_admin',
    'التذاكر تُفتح عبر نظام الدعم في البوابة الأكاديمية. للدعم التقني (بلاك بورد، أنظمة): IT Help Desk. للشؤون الأكاديمية: شؤون الطلاب في كليتك.',
    'عبر البوابة — IT للتقني، كليتك للأكاديمي',
    'draft',
    'semi_official',
    0.70,
    'always',
    FALSE
),

-- 12. grade_query — how to appeal a grade
(
    'كيف أعترض على درجتي؟',
    ARRAY['اعتراض على نتيجة', 'مراجعة الدرجة', 'طلب مراجعة ورقة'],
    'grade_query',
    'الاعتراض على الدرجات يتم خلال فترة الاعتراض المحددة في التقويم الأكاديمي. قدّم الطلب عبر البوابة، ستُرسل الورقة للدكتور لمراجعتها.',
    'عبر البوابة في فترة الاعتراض المحددة',
    'draft',
    'semi_official',
    0.70,
    'grade_release_period',
    TRUE
),

-- 13. attendance_excuse — maximum absence percentage
(
    'كم نسبة الغياب المسموح بها؟',
    ARRAY['حد الغياب', 'نسبة الحضور المطلوبة', 'كم مرة أتغيب'],
    'attendance_excuse',
    'وفق لوائح الجامعة، الحد الأقصى للغياب عادةً 25% من المحاضرات. تجاوز هذه النسبة قد يُؤدي إلى الحرمان من الاختبار أو رسوب في المادة.',
    '25% حد أقصى — تجاوزه يُعرّضك للحرمان',
    'draft',
    'semi_official',
    0.70,
    'always',
    TRUE
),

-- 14. exam_procedure — when exam schedule is announced
(
    'متى تُعلن جداول الاختبارات؟',
    ARRAY['موعد جدول الاختبارات', 'متى ينزل الجدول'],
    'exam_procedure',
    'جداول الاختبارات عادةً تُعلن قبل 2 إلى 3 أسابيع من بدء الاختبارات عبر البوابة الأكاديمية ومنصة بلاك بورد.',
    'قبل 2-3 أسابيع من الاختبارات — تابع البوابة',
    'draft',
    'community',
    0.65,
    'always',
    TRUE
),

-- 15. payment — consequences of late payment
(
    'وش يصير لو ما سددت في الوقت؟',
    ARRAY['تأخير السداد', 'عقوبة عدم السداد', 'انتهاء فترة السداد'],
    'payment',
    'التأخر في السداد قد يُؤدي إلى تجميد التسجيل أو حذف المواد المسجلة. تواصل مع إدارة الشؤون المالية في أقرب وقت لتجنب الغرامات.',
    'تجميد التسجيل وحذف المواد المحتمل',
    'draft',
    'community',
    0.60,
    'always',
    TRUE
),

-- 16. schedule — lecture timetable
(
    'كيف أعرف توقيت المحاضرات؟',
    ARRAY['جدول المحاضرات', 'متى محاضراتي', 'أوقات الحضور'],
    'schedule',
    'جدول محاضراتك يظهر في البوابة الأكاديمية بعد إتمام التسجيل. كذلك في منصة بلاك بورد تحت كل مادة.',
    'البوابة الأكاديمية أو بلاك بورد بعد التسجيل',
    'draft',
    'community',
    0.70,
    'always',
    FALSE
),

-- 17. general_admin — enrollment certificate
(
    'كيف أحصل على شهادة قيد؟',
    ARRAY['شهادة تسجيل', 'وثيقة قيد', 'طلب شهادة'],
    'general_admin',
    'شهادة القيد تُطلب عبر خدمات الطلاب في البوابة الأكاديمية. عادةً تستغرق 1 إلى 3 أيام عمل للإصدار. يمكن طلبها إلكترونياً وتستلم PDF موقعة.',
    'عبر البوابة → طلب شهادة — 1-3 أيام عمل',
    'draft',
    'community',
    0.70,
    'always',
    FALSE
),

-- 18. exam_procedure — late entry to exam hall
(
    'هل يُسمح بالدخول للاختبار بعد بدايته؟',
    ARRAY['التأخر عن الاختبار', 'دخول متأخر للاختبار'],
    'exam_procedure',
    'عادةً يُسمح بالدخول للاختبار خلال أول 15 دقيقة من البداية فقط. بعدها لا يُسمح بالدخول وفق لوائح الاختبارات. تأكد من وصولك قبل الموعد بـ 15 دقيقة على الأقل.',
    'أول 15 دقيقة فقط — وصول مبكر ضروري',
    'draft',
    'community',
    0.70,
    'exam_period',
    TRUE
),

-- 19. grade_query — GPA calculation (official)
(
    'كيف يُحسب المعدل التراكمي GPA؟',
    ARRAY['حساب المعدل', 'كيف تحتسب الدرجات', 'نظام الدرجات'],
    'grade_query',
    'المعدل التراكمي يُحسب من مجموع نقاط الجودة (درجة × عدد الساعات) مقسوماً على مجموع الساعات. الدرجات: A+=4.0, A=4.0, B+=3.5, B=3.0, C+=2.5, C=2.0, D+=1.5, D=1.0, F=0.0',
    'مجموع (درجة × ساعات) ÷ مجموع الساعات',
    'draft',
    'official',
    0.92,
    'always',
    FALSE
),

-- 20. blackboard_technical — how to submit an assignment
(
    'كيف أُسلّم الواجب عبر بلاك بورد؟',
    ARRAY['تسليم الواجب', 'رفع ملف في بلاك بورد', 'طريقة التسليم'],
    'blackboard_technical',
    'لتسليم الواجب عبر بلاك بورد: 1) ادخل على المادة، 2) اذهب إلى Assessments أو Assignments، 3) اضغط على الواجب المطلوب، 4) ارفع الملف أو اكتب الإجابة، 5) اضغط Submit وانتظر رسالة التأكيد.',
    'المادة → Assessments → ارفع → Submit → انتظر التأكيد',
    'draft',
    'community',
    0.70,
    'always',
    FALSE
);

-- ── END OF MIGRATION 046 ──────────────────────────────────────
