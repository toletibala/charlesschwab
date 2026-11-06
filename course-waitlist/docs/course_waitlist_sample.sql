PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS waitlist_entries;
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS learner_course_history;
DROP TABLE IF EXISTS course_offerings;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS learners;


CREATE TABLE learners (
    learner_id   TEXT PRIMARY KEY,                 
    full_name    TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,            
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE courses (
    course_code  TEXT PRIMARY KEY,                 
    title        TEXT NOT NULL
);

CREATE TABLE course_offerings (
    offering_id            TEXT PRIMARY KEY,       
    course_code            TEXT NOT NULL REFERENCES courses(course_code),
    prerequisite_course    TEXT REFERENCES courses(course_code),  
    capacity               INTEGER NOT NULL CHECK (capacity > 0),
    version                INTEGER NOT NULL DEFAULT 0  
);

CREATE TABLE learner_course_history (
    learner_id   TEXT NOT NULL REFERENCES learners(learner_id),
    course_code  TEXT NOT NULL REFERENCES courses(course_code),
    status       TEXT NOT NULL CHECK (status IN ('PASSED','FAILED','IN_PROGRESS','REVOKED')),
    completed_on TEXT,
    PRIMARY KEY (learner_id, course_code)
);

CREATE TABLE enrollments (
    enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    offering_id   TEXT NOT NULL REFERENCES course_offerings(offering_id),
    learner_id    TEXT NOT NULL REFERENCES learners(learner_id),
    status        TEXT NOT NULL CHECK (status IN ('ENROLLED','DROPPED')),
    source        TEXT NOT NULL CHECK (source IN ('DIRECT','WAITLIST_PROMOTION')),
    enrolled_at   TEXT NOT NULL,
    dropped_at    TEXT
);
-- One ACTIVE enrollment per learner per offering (history rows allowed)
CREATE UNIQUE INDEX ux_enrollment_active
    ON enrollments(offering_id, learner_id) WHERE status = 'ENROLLED';

CREATE TABLE waitlist_entries (
    waitlist_id   INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotonic => FIFO tiebreak
    offering_id   TEXT NOT NULL REFERENCES course_offerings(offering_id),
    learner_id    TEXT NOT NULL REFERENCES learners(learner_id),
    status        TEXT NOT NULL CHECK (status IN ('WAITING','PROMOTED','SKIPPED','CANCELLED')),
    joined_at     TEXT NOT NULL,
    resolved_at   TEXT,
    resolution_note TEXT
);
-- One WAITING entry per learner per offering
CREATE UNIQUE INDEX ux_waitlist_active
    ON waitlist_entries(offering_id, learner_id) WHERE status = 'WAITING';
CREATE INDEX ix_waitlist_fifo
    ON waitlist_entries(offering_id, status, joined_at, waitlist_id);

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------
INSERT INTO courses (course_code, title) VALUES
  ('CS101', 'Introduction to Programming'),
  ('CS201', 'Data Structures'),
  ('MA101', 'Discrete Mathematics');

-- Main offering under test: CS201, capacity 3, requires CS101
INSERT INTO course_offerings (offering_id, course_code, prerequisite_course, capacity, version) VALUES
  ('CS201-2026F-01', 'CS201', 'CS101', 3, 7),
  -- Secondary offering with spare capacity and no prerequisite (enroll-success case)
  ('MA101-2026F-01', 'MA101', NULL,    5, 1);

-- ---------------------------------------------------------------------
-- Learners (dummy data)
-- ---------------------------------------------------------------------
INSERT INTO learners (learner_id, full_name, email, created_at) VALUES
  ('L001', 'Aarav Sharma',   'aarav.sharma@example.com',   '2026-08-01 09:00:00'),
  ('L002', 'Priya Nair',     'priya.nair@example.com',     '2026-08-01 09:05:00'),
  ('L003', 'Rohan Gupta',    'rohan.gupta@example.com',    '2026-08-01 09:10:00'),
  ('L004', 'Sneha Reddy',    'sneha.reddy@example.com',    '2026-08-02 10:00:00'),
  ('L005', 'Vikram Iyer',    'vikram.iyer@example.com',    '2026-08-02 10:15:00'),
  ('L006', 'Ananya Das',     'ananya.das@example.com',     '2026-08-02 11:00:00'),
  ('L007', 'Karthik Menon',  'karthik.menon@example.com',  '2026-08-03 08:30:00'),
  ('L008', 'Meera Pillai',   'meera.pillai@example.com',   '2026-08-03 09:45:00'),
  ('L009', 'Arjun Patel',    'arjun.patel@example.com',    '2026-08-04 14:00:00'),
  ('L010', 'Divya Krishnan', 'divya.krishnan@example.com', '2026-08-04 15:20:00');

-- ---------------------------------------------------------------------
-- Prerequisite history (CS101)
-- ---------------------------------------------------------------------
INSERT INTO learner_course_history (learner_id, course_code, status, completed_on) VALUES
  ('L001', 'CS101', 'PASSED',      '2026-05-20'),
  ('L002', 'CS101', 'PASSED',      '2026-05-20'),
  ('L003', 'CS101', 'PASSED',      '2026-05-21'),
  ('L004', 'CS101', 'PASSED',      '2026-05-21'),
  ('L005', 'CS101', 'REVOKED',     '2026-05-22'), -- passed earlier, joined waitlist, then revoked -> SKIP at front
  ('L006', 'CS101', 'PASSED',      '2026-05-22'),
  ('L007', 'CS101', 'FAILED',      '2026-05-23'), -- prerequisite failure on enroll attempt
  ('L008', 'CS101', 'IN_PROGRESS', NULL),         -- not yet completed -> ineligible
  ('L010', 'CS101', 'PASSED',      '2026-05-25');
  -- L009 has no CS101 record at all -> ineligible

-- ---------------------------------------------------------------------
-- Enrollments for CS201-2026F-01 (capacity 3 -> currently FULL)
-- ---------------------------------------------------------------------
INSERT INTO enrollments (offering_id, learner_id, status, source, enrolled_at, dropped_at) VALUES
  ('CS201-2026F-01', 'L001', 'ENROLLED', 'DIRECT',             '2026-09-01 10:00:00', NULL),
  ('CS201-2026F-01', 'L002', 'ENROLLED', 'DIRECT',             '2026-09-01 10:02:00', NULL),
  -- L010 enrolled, dropped (history row), then got the seat back via the waitlist
  ('CS201-2026F-01', 'L010', 'DROPPED',  'DIRECT',             '2026-09-01 10:03:00', '2026-09-05 12:00:00'),
  ('CS201-2026F-01', 'L003', 'ENROLLED', 'WAITLIST_PROMOTION', '2026-09-05 12:00:00', NULL);

-- Enrollment in the secondary offering (spare capacity)
INSERT INTO enrollments (offering_id, learner_id, status, source, enrolled_at, dropped_at) VALUES
  ('MA101-2026F-01', 'L009', 'ENROLLED', 'DIRECT', '2026-09-02 09:00:00', NULL);

-- ---------------------------------------------------------------------
-- Waitlist for CS201-2026F-01 (FIFO by joined_at, then waitlist_id)
-- ---------------------------------------------------------------------
INSERT INTO waitlist_entries (offering_id, learner_id, status, joined_at, resolved_at, resolution_note) VALUES
  -- Historical: L003 was first in line and was promoted when L010 dropped
  ('CS201-2026F-01', 'L003', 'PROMOTED',  '2026-09-01 10:05:00', '2026-09-05 12:00:00', 'Promoted after L010 dropped'),
  -- Active queue (in FIFO order):
  ('CS201-2026F-01', 'L005', 'WAITING',   '2026-09-01 10:10:00', NULL, NULL),  -- front, but prereq now REVOKED -> will be SKIPPED
  ('CS201-2026F-01', 'L004', 'WAITING',   '2026-09-01 10:20:00', NULL, NULL),  -- next eligible -> should be promoted
  ('CS201-2026F-01', 'L006', 'WAITING',   '2026-09-01 10:30:00', NULL, NULL),  -- eligible, stays waiting
  -- Historical: L010 joined, then cancelled their waitlist entry
  ('CS201-2026F-01', 'L010', 'CANCELLED', '2026-09-06 08:00:00', '2026-09-07 08:00:00', 'Learner withdrew from waitlist');

-- ---------------------------------------------------------------------
-- Handy views for checking invariants
-- ---------------------------------------------------------------------
CREATE VIEW v_offering_seats AS
SELECT o.offering_id,
       o.capacity,
       COUNT(e.enrollment_id)                AS enrolled_count,
       o.capacity - COUNT(e.enrollment_id)   AS seats_available
FROM course_offerings o
LEFT JOIN enrollments e
       ON e.offering_id = o.offering_id AND e.status = 'ENROLLED'
GROUP BY o.offering_id, o.capacity;

CREATE VIEW v_waitlist_queue AS
SELECT w.offering_id,
       ROW_NUMBER() OVER (PARTITION BY w.offering_id ORDER BY w.joined_at, w.waitlist_id) AS position,
       w.learner_id,
       l.full_name,
       w.joined_at,
       CASE WHEN o.prerequisite_course IS NULL THEN 1
            WHEN h.status = 'PASSED' THEN 1 ELSE 0 END AS is_eligible
FROM waitlist_entries w
JOIN learners l          ON l.learner_id = w.learner_id
JOIN course_offerings o  ON o.offering_id = w.offering_id
LEFT JOIN learner_course_history h
       ON h.learner_id = w.learner_id AND h.course_code = o.prerequisite_course
WHERE w.status = 'WAITING';

-- =====================================================================
-- Test-scenario map (what each dummy record lets you exercise)
-- ---------------------------------------------------------------------
--  Available capacity     : enroll L009 or L001 into MA101-2026F-01 (5 seats, no prereq)
--  Full capacity          : enroll L006/L004 into CS201 -> already full (3/3); joins waitlist
--  Duplicate enroll       : enroll L001 into CS201 again -> ALREADY_ENROLLED, no new row
--  Duplicate waitlist     : enroll L004 into CS201 again -> ALREADY_WAITLISTED, no new row
--  Prerequisite failure   : L007 (FAILED), L008 (IN_PROGRESS), L009 (no record) -> rejected
--  FIFO promotion + skip  : drop L001 from CS201 -> L005 SKIPPED (revoked), L004 PROMOTED,
--                           L006 remains WAITING at position 1
--  Repeated drop          : drop L010 from CS201 -> NOT_ENROLLED (already DROPPED), no change
--  Invalid requests       : unknown learner 'L999', unknown offering 'XX999', NULL ids
--  Re-enroll after drop   : allowed by partial unique index (L010 has a DROPPED history row)
--  Concurrency            : version column on course_offerings for optimistic locking
-- =====================================================================
