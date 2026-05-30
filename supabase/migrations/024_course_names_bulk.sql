-- 024_course_names_bulk.sql
-- Populate name_en for courses with NULL names.
-- Source: official SEU study plan PDFs (Sep 2023 and Dec 2025 editions).
-- Only courses confirmed from official English-language documents are included.

DO $$
DECLARE
  tid UUID := '00000000-0000-0000-0000-000000000001';
BEGIN

  -- IT courses (BS Information Technology, Sep 2023 plan)
  UPDATE inst_courses SET name_en = 'Introduction to Information Technology' WHERE tenant_id = tid AND code = 'IT110';
  UPDATE inst_courses SET name_en = 'Introduction to IT and IS'              WHERE tenant_id = tid AND code = 'IT231';
  UPDATE inst_courses SET name_en = 'Object Oriented Programming'            WHERE tenant_id = tid AND code = 'IT232';
  UPDATE inst_courses SET name_en = 'Computer Organization'                  WHERE tenant_id = tid AND code = 'IT233';
  UPDATE inst_courses SET name_en = 'Operating Systems'                      WHERE tenant_id = tid AND code = 'IT241';
  UPDATE inst_courses SET name_en = 'Introduction to Database'               WHERE tenant_id = tid AND code = 'IT244';
  UPDATE inst_courses SET name_en = 'Data Structure'                         WHERE tenant_id = tid AND code = 'IT245';
  UPDATE inst_courses SET name_en = 'Computer Networks'                      WHERE tenant_id = tid AND code = 'IT351';
  UPDATE inst_courses SET name_en = 'Human Computer Interaction'             WHERE tenant_id = tid AND code = 'IT352';
  UPDATE inst_courses SET name_en = 'System Analysis and Design'             WHERE tenant_id = tid AND code = 'IT353';
  UPDATE inst_courses SET name_en = 'Database Management Systems'            WHERE tenant_id = tid AND code = 'IT354';
  UPDATE inst_courses SET name_en = 'Web Technologies'                       WHERE tenant_id = tid AND code = 'IT361';
  UPDATE inst_courses SET name_en = 'IT Project Management'                  WHERE tenant_id = tid AND code = 'IT362';
  UPDATE inst_courses SET name_en = 'Network Management'                     WHERE tenant_id = tid AND code = 'IT363';
  UPDATE inst_courses SET name_en = 'IT Entrepreneurship and Innovation'     WHERE tenant_id = tid AND code = 'IT364';
  UPDATE inst_courses SET name_en = 'Enterprise Systems'                     WHERE tenant_id = tid AND code = 'IT365';
  UPDATE inst_courses SET name_en = 'Introduction to Cyber Security and Digital Crime' WHERE tenant_id = tid AND code = 'IT474';
  UPDATE inst_courses SET name_en = 'Decision Support Systems'               WHERE tenant_id = tid AND code = 'IT475';
  UPDATE inst_courses SET name_en = 'IT Security and Policies'               WHERE tenant_id = tid AND code = 'IT476';
  UPDATE inst_courses SET name_en = 'Network Security'                       WHERE tenant_id = tid AND code = 'IT478';
  UPDATE inst_courses SET name_en = 'Wireless Sensor Networks'               WHERE tenant_id = tid AND code = 'IT484';
  UPDATE inst_courses SET name_en = 'Mobile Application Development'         WHERE tenant_id = tid AND code = 'IT487';
  UPDATE inst_courses SET name_en = 'Cyber Forensics'                        WHERE tenant_id = tid AND code = 'IT488';

  -- Finance courses (BSBA Finance specialization)
  UPDATE inst_courses SET name_en = 'Corporate Finance'                      WHERE tenant_id = tid AND code = 'FIN201';
  UPDATE inst_courses SET name_en = 'Risk Management'                        WHERE tenant_id = tid AND code = 'FIN301';
  UPDATE inst_courses SET name_en = 'Banks Management'                       WHERE tenant_id = tid AND code = 'FIN401';
  UPDATE inst_courses SET name_en = 'Financial Institutions and Markets'     WHERE tenant_id = tid AND code = 'FIN402';
  UPDATE inst_courses SET name_en = 'Investments'                            WHERE tenant_id = tid AND code = 'FIN403';
  UPDATE inst_courses SET name_en = 'Financial Derivatives'                  WHERE tenant_id = tid AND code = 'FIN405';
  UPDATE inst_courses SET name_en = 'International Finance'                  WHERE tenant_id = tid AND code = 'FIN406';
  UPDATE inst_courses SET name_en = 'Real Estate Finance'                    WHERE tenant_id = tid AND code = 'FIN414';
  UPDATE inst_courses SET name_en = 'Islamic Finance'                        WHERE tenant_id = tid AND code = 'FIN416';
  UPDATE inst_courses SET name_en = 'Small Business Financing'               WHERE tenant_id = tid AND code = 'FIN421';
  UPDATE inst_courses SET name_en = 'Portfolio Management'                   WHERE tenant_id = tid AND code = 'FIN424';

  -- Accounting courses (BSBA Accounting specialization)
  UPDATE inst_courses SET name_en = 'Financial Accounting'                   WHERE tenant_id = tid AND code = 'ACCT201';
  UPDATE inst_courses SET name_en = 'Advanced Financial Accounting'          WHERE tenant_id = tid AND code = 'ACCT302';
  UPDATE inst_courses SET name_en = 'Government and Non-Profit Accounting'   WHERE tenant_id = tid AND code = 'ACCT321';
  UPDATE inst_courses SET name_en = 'Auditing Principles and Procedures'     WHERE tenant_id = tid AND code = 'ACCT401';
  UPDATE inst_courses SET name_en = 'Introduction to Accounting Information Systems' WHERE tenant_id = tid AND code = 'ACCT402';
  UPDATE inst_courses SET name_en = 'Accounting Research and Practice'       WHERE tenant_id = tid AND code = 'ACCT403';
  UPDATE inst_courses SET name_en = 'Tax and Zakat Accounting'               WHERE tenant_id = tid AND code = 'ACCT422';

  -- Older ACCT code variants (same content, different prefix)
  UPDATE inst_courses SET name_en = 'Principles of Accounting'               WHERE tenant_id = tid AND code = 'ACC101';
  UPDATE inst_courses SET name_en = 'Cost Accounting'                        WHERE tenant_id = tid AND code = 'ACC301';

  -- Data Science courses (BS Data Science, Sep 2023 plan)
  UPDATE inst_courses SET name_en = 'Introduction to Data Science Programming' WHERE tenant_id = tid AND code = 'DS231';
  UPDATE inst_courses SET name_en = 'Advanced Data Science Programming'        WHERE tenant_id = tid AND code = 'DS242';
  UPDATE inst_courses SET name_en = 'Computer Architecture and Organization'   WHERE tenant_id = tid AND code = 'DS243';
  UPDATE inst_courses SET name_en = 'System Analysis and Design'               WHERE tenant_id = tid AND code = 'DS361';

  -- E-Commerce courses (BSBA E-Commerce specialization)
  UPDATE inst_courses SET name_en = 'Digital Marketing'                      WHERE tenant_id = tid AND code = 'ECOM301';
  UPDATE inst_courses SET name_en = 'Social Media Marketing'                 WHERE tenant_id = tid AND code = 'ECOM322';
  UPDATE inst_courses SET name_en = 'E-Supply Chain Management'              WHERE tenant_id = tid AND code = 'ECOM402';

  -- Management courses (BA Management plan, Dec 2025)
  UPDATE inst_courses SET name_en = 'Management of Technology'               WHERE tenant_id = tid AND code = 'MGT325';
  UPDATE inst_courses SET name_en = 'Quality Management'                     WHERE tenant_id = tid AND code = 'MGT424';
  UPDATE inst_courses SET name_en = 'Spreadsheet Decision Modeling'          WHERE tenant_id = tid AND code = 'MGT425';

  -- Islamic Studies (university requirements — confirmed from ECOM and BA plans)
  UPDATE inst_courses SET name_en = 'Introduction to Islamic Culture'        WHERE tenant_id = tid AND code = 'ISLAM101';
  UPDATE inst_courses SET name_en = 'Professional Conduct and Ethics in Islam' WHERE tenant_id = tid AND code = 'ISLAM102';
  UPDATE inst_courses SET name_en = 'Islamic Economic System'                WHERE tenant_id = tid AND code = 'ISLAM103';
  UPDATE inst_courses SET name_en = 'Social System and Human Rights in Islam' WHERE tenant_id = tid AND code = 'ISLAM104';

  -- CS course (confirmed from document chunks — OOP/data structures content)
  UPDATE inst_courses SET name_en = 'Introduction to Object Oriented Programming' WHERE tenant_id = tid AND code = 'CS141';

  -- Calculus (confirmed from document chunks — derivative formulas content)
  UPDATE inst_courses SET name_en = 'Calculus'                               WHERE tenant_id = tid AND code = 'MATH241';

  -- Law courses (confirmed from document chunks)
  UPDATE inst_courses SET name_en = 'E-Commerce Law'                         WHERE tenant_id = tid AND code = 'LAW402';
  UPDATE inst_courses SET name_en = 'Law of Information Technology'          WHERE tenant_id = tid AND code = 'LOW402';

  -- Accounting — foreign currency journal entries content
  UPDATE inst_courses SET name_en = 'Intermediate Accounting'                WHERE tenant_id = tid AND code = 'ACT302';

  -- Health Care Management (management/authority content confirmed)
  UPDATE inst_courses SET name_en = 'Health Care Organization Management'    WHERE tenant_id = tid AND code = 'HCM113';

END $$;
