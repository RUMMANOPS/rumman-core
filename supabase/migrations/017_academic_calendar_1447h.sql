-- Migration 017: Replace placeholder calendar data with correct full 1447H / 2025-2026 calendar
-- Source: https://www.seu.edu.sa/ar/academic-calendar/
-- Extracted: 2026-05-29

-- Remove placeholder rows (they had wrong dates)
DELETE FROM academic_calendar
WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
  AND academic_year = '1447';

-- ── Semester 1 (first) ────────────────────────────────────────────────────────
INSERT INTO academic_calendar (tenant_id, academic_year, semester, event_type, event_name_ar, event_name_en, start_date, end_date) VALUES
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'early_registration',     'التسجيل المبكر',                    'Early Registration',                    '2025-08-10', '2025-08-14'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'course_registration',    'تسجيل المقررات',                    'Course Registration',                   '2025-08-17', '2025-08-23'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'tuition_payment',        'سداد الرسوم الدراسية',               'Tuition Payment Window',                '2025-08-17', '2025-09-04'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'semester_start',         'بداية الدراسة',                     'Semester 1 Start',                      '2025-08-24', NULL),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'midterm_exam',           'الاختبارات الفصلية (بكالوريوس)',     'Midterm Exams – Bachelor',              '2025-10-09', '2025-10-18'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'midterm_exam_firstyear', 'الاختبارات الفصلية (السنة الأولى)',  'Midterm Exams – First Year',            '2025-10-05', '2025-10-08'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'withdrawal_deadline',    'آخر موعد للانسحاب من مقرر',          'Course Withdrawal Deadline',            '2025-11-27', NULL),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'final_exam',             'الاختبارات النهائية',                'Final Exams',                           '2025-12-14', '2026-01-01'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'exam_appeal_deadline',   'آخر موعد للاعتراض على النتائج',     'Exam Appeal Deadline',                  '2025-12-21', '2026-01-06'),
('00000000-0000-0000-0000-000000000001', '1447', 'first', 'semester_end',           'نهاية الفصل الدراسي الأول',          'Semester 1 End',                        '2026-01-09', NULL),

-- ── Semester 2 (second) ───────────────────────────────────────────────────────
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'course_registration',    'بدء تسجيل المقررات',                'Course Registration',                   '2026-01-11', '2026-01-24'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'tuition_payment',        'سداد الرسوم الدراسية',               'Tuition Payment Window',                '2026-01-11', '2026-02-02'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'semester_start',         'بداية الدراسة',                     'Semester 2 Start',                      '2026-01-18', NULL),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'holiday_eid_fitr',       'إجازة عيد الفطر',                    'Eid Al-Fitr Holiday',                   '2026-03-06', '2026-03-28'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'midterm_exam_firstyear', 'الاختبارات الفصلية (السنة الأولى)',  'Midterm Exams – First Year',            '2026-03-29', '2026-04-01'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'midterm_exam',           'الاختبارات الفصلية (بكالوريوس)',     'Midterm Exams – Bachelor',              '2026-04-02', '2026-04-16'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'withdrawal_deadline',    'آخر موعد للانسحاب من مقرر',          'Course Withdrawal Deadline',            '2026-04-23', NULL),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'holiday_eid_adha',       'إجازة عيد الأضحى',                   'Eid Al-Adha Holiday',                   '2026-05-22', '2026-06-01'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'final_exam_firstyear',   'الاختبارات النهائية (السنة الأولى)', 'Final Exams – First Year',              '2026-05-17', '2026-05-20'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'final_exam',             'الاختبارات النهائية (بكالوريوس)',    'Final Exams – Bachelor',                '2026-06-05', '2026-06-12'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'exam_appeal_deadline',   'آخر موعد للاعتراض على النتائج',     'Exam Appeal Deadline',                  '2026-05-20', '2026-06-25'),
('00000000-0000-0000-0000-000000000001', '1447', 'second', 'semester_end',           'بداية إجازة نهاية العام الدراسي',    'Summer Break / Year End',               '2026-06-18', NULL);
