"""Tests for enroll / drop / waitlist.

Every test builds its own small service. After scenarios that change state we
also check check_invariants() == [] to make sure nothing got corrupted.

Run:  python -m unittest discover -s tests -v
"""

import threading
import unittest

from waitlist import InMemoryStore, Learner, Offering, PrereqStatus, RegistrationService, ResultCode

OFFERING = "CS201-2026F-01"


def make_service(capacity=2, prereq="CS101", passed=(), other=None):
    """Build a service.

    passed : learner ids who PASSED the prerequisite
    other  : {learner_id: PrereqStatus} for learners with a non-passing status
    Learners in neither group exist but have no prerequisite record.
    """
    store = InMemoryStore(Offering(OFFERING, "CS201", capacity, prereq))
    for i in range(1, 21):
        store.add_learner(Learner(f"L{i:03d}", f"Learner {i}"))
    for learner_id in passed:
        store.set_course_status(learner_id, "CS101", PrereqStatus.PASSED)
    for learner_id, status in (other or {}).items():
        store.set_course_status(learner_id, "CS101", status)
    return RegistrationService(store), store


ALL = [f"L{i:03d}" for i in range(1, 21)]


class BaseTest(unittest.TestCase):
    def assertConsistent(self, service):
        self.assertEqual(service.check_invariants(), [])


# enrollment with available capacity
class AvailableCapacityTests(BaseTest):
    def test_enroll_succeeds_when_seat_free_and_prereq_passed(self):
        service, _ = make_service(capacity=2, passed=ALL)
        result = service.enroll(OFFERING, "L001")
        self.assertEqual(result.code, ResultCode.ENROLLED)
        self.assertTrue(result.ok)
        self.assertEqual(service.snapshot()["enrolled"], ["L001"])
        self.assertEqual(service.snapshot()["seats_available"], 1)
        self.assertConsistent(service)

    def test_offering_without_prerequisite_accepts_anyone(self):
        service, _ = make_service(capacity=2, prereq=None)  # nobody has history
        self.assertEqual(service.enroll(OFFERING, "L009").code, ResultCode.ENROLLED)


# full capacity -> waitlist once; duplicates create nothing
class FullCapacityAndDuplicateTests(BaseTest):
    def setUp(self):
        self.service, self.store = make_service(capacity=2, passed=ALL)
        self.service.enroll(OFFERING, "L001")
        self.service.enroll(OFFERING, "L002")

    def test_full_course_puts_eligible_learner_on_waitlist(self):
        result = self.service.enroll(OFFERING, "L003")
        self.assertEqual(result.code, ResultCode.WAITLISTED)
        self.assertEqual(result.waitlist_position, 1)
        snap = self.service.snapshot()
        self.assertEqual(snap["enrolled"], ["L001", "L002"])
        self.assertEqual(snap["waitlist"], ["L003"])
        self.assertConsistent(self.service)

    def test_duplicate_enroll_of_enrolled_learner_creates_no_record(self):
        rows_before = len(self.store.enrollments)
        result = self.service.enroll(OFFERING, "L001")
        self.assertEqual(result.code, ResultCode.ALREADY_ENROLLED)
        self.assertEqual(len(self.store.enrollments), rows_before)
        self.assertEqual(self.store.waitlist, [])

    def test_duplicate_enroll_of_waitlisted_learner_creates_no_record(self):
        self.service.enroll(OFFERING, "L003")
        result = self.service.enroll(OFFERING, "L003")
        self.assertEqual(result.code, ResultCode.ALREADY_WAITLISTED)
        self.assertEqual(result.waitlist_position, 1)
        self.assertEqual(len(self.store.waitlist), 1)
        self.assertConsistent(self.service)


# drop opens a seat; earliest eligible learner is promoted (FIFO)
class FifoPromotionTests(BaseTest):
    def test_drop_promotes_earliest_waitlisted_learner(self):
        service, _ = make_service(capacity=2, passed=ALL)
        for learner_id in ["L001", "L002", "L003", "L004", "L005"]:
            service.enroll(OFFERING, learner_id)

        result = service.drop(OFFERING, "L001")
        self.assertEqual(result.code, ResultCode.DROPPED)
        self.assertEqual(result.promoted, ("L003",))
        snap = service.snapshot()
        self.assertEqual(snap["enrolled"], ["L002", "L003"])
        self.assertEqual(snap["waitlist"], ["L004", "L005"])
        self.assertConsistent(service)

        # A second drop serves the next person in order.
        self.assertEqual(service.drop(OFFERING, "L002").promoted, ("L004",))
        self.assertEqual(service.snapshot()["waitlist"], ["L005"])

    def test_drop_with_empty_waitlist_just_frees_the_seat(self):
        service, _ = make_service(capacity=2, passed=ALL)
        service.enroll(OFFERING, "L001")
        result = service.drop(OFFERING, "L001")
        self.assertEqual(result.promoted, ())
        self.assertEqual(service.snapshot()["seats_available"], 2)

    def test_re_enroll_after_drop_is_a_new_request(self):
        service, _ = make_service(capacity=1, passed=ALL)
        service.enroll(OFFERING, "L001")
        service.enroll(OFFERING, "L002")  # waitlisted
        service.drop(OFFERING, "L001")    # L002 promoted
        result = service.enroll(OFFERING, "L001")
        self.assertEqual(result.code, ResultCode.WAITLISTED)  # back of the queue
        self.assertConsistent(service)


class PrerequisiteTests(BaseTest):
    def test_failed_in_progress_revoked_and_missing_are_all_rejected(self):
        service, store = make_service(capacity=5, other={
            "L001": PrereqStatus.FAILED,
            "L002": PrereqStatus.IN_PROGRESS,
            "L003": PrereqStatus.REVOKED,
        })  # L004 has no record
        for learner_id in ["L001", "L002", "L003", "L004"]:
            result = service.enroll(OFFERING, learner_id)
            self.assertEqual(result.code, ResultCode.PREREQUISITE_NOT_MET, learner_id)
        self.assertEqual(store.enrollments, [])
        self.assertEqual(store.waitlist, [])

    def test_ineligible_learner_is_rejected_even_when_course_is_full(self):
        service, store = make_service(capacity=1, passed=["L001"])
        service.enroll(OFFERING, "L001")
        self.assertEqual(service.enroll(OFFERING, "L002").code, ResultCode.PREREQUISITE_NOT_MET)
        self.assertEqual(store.waitlist, [])  # not waitlisted either

    def test_learner_ineligible_at_front_is_skipped_and_next_is_promoted(self):
        service, store = make_service(capacity=1, passed=ALL)
        for learner_id in ["L001", "L002", "L003", "L004"]:
            service.enroll(OFFERING, learner_id)  # L002, L003, L004 wait
        store.set_course_status("L002", "CS101", PrereqStatus.REVOKED)

        result = service.drop(OFFERING, "L001")
        self.assertEqual(result.skipped, ("L002",))
        self.assertEqual(result.promoted, ("L003",))
        snap = service.snapshot()
        self.assertEqual(snap["enrolled"], ["L003"])
        self.assertEqual(snap["waitlist"], ["L004"])  # L002 has left the queue
        self.assertConsistent(service)

    def test_skip_is_permanent_even_if_learner_becomes_eligible_again(self):
        service, store = make_service(capacity=1, passed=ALL)
        for learner_id in ["L001", "L002", "L003"]:
            service.enroll(OFFERING, learner_id)
        store.set_course_status("L002", "CS101", PrereqStatus.REVOKED)
        service.drop(OFFERING, "L001")                                  # L002 skipped
        store.set_course_status("L002", "CS101", PrereqStatus.PASSED)  # fixed later
        service.drop(OFFERING, "L003")
        self.assertNotIn("L002", service.snapshot()["enrolled"])        # not resurrected

    def test_all_waiting_learners_ineligible_leaves_seat_free(self):
        service, store = make_service(capacity=1, passed=ALL)
        for learner_id in ["L001", "L002", "L003"]:
            service.enroll(OFFERING, learner_id)
        store.set_course_status("L002", "CS101", PrereqStatus.REVOKED)
        store.set_course_status("L003", "CS101", PrereqStatus.FAILED)
        result = service.drop(OFFERING, "L001")
        self.assertEqual(result.skipped, ("L002", "L003"))
        self.assertEqual(result.promoted, ())
        snap = service.snapshot()
        self.assertEqual((snap["enrolled"], snap["waitlist"], snap["seats_available"]), ([], [], 1))
        self.assertConsistent(service)

    def test_only_the_front_is_evaluated(self):
        # L003 is ineligible but sits behind the promoted learner, so it stays.
        service, store = make_service(capacity=1, passed=ALL)
        for learner_id in ["L001", "L002", "L003"]:
            service.enroll(OFFERING, learner_id)
        store.set_course_status("L003", "CS101", PrereqStatus.REVOKED)
        result = service.drop(OFFERING, "L001")
        self.assertEqual(result.promoted, ("L002",))
        self.assertEqual(result.skipped, ())
        self.assertEqual(service.snapshot()["waitlist"], ["L003"])


# invalid requests and repeated drops leave state unchanged
class InvalidAndRepeatedDropTests(BaseTest):
    def setUp(self):
        self.service, self.store = make_service(capacity=1, passed=ALL)
        self.service.enroll(OFFERING, "L001")
        self.service.enroll(OFFERING, "L002")  # waiting

    def assertUnchanged(self, before):
        self.assertEqual(self.service.snapshot(), before)
        self.assertConsistent(self.service)

    def test_repeated_drop_returns_not_enrolled(self):
        self.service.drop(OFFERING, "L001")  # L002 promoted
        before = self.service.snapshot()
        result = self.service.drop(OFFERING, "L001")
        self.assertEqual(result.code, ResultCode.NOT_ENROLLED)
        self.assertIn("already dropped", result.message)
        self.assertUnchanged(before)

    def test_drop_by_waitlisted_learner_changes_nothing(self):
        before = self.service.snapshot()
        result = self.service.drop(OFFERING, "L002")
        self.assertEqual(result.code, ResultCode.NOT_ENROLLED)
        self.assertIn("waitlist", result.message)
        self.assertUnchanged(before)

    def test_drop_by_learner_who_never_enrolled(self):
        before = self.service.snapshot()
        self.assertEqual(self.service.drop(OFFERING, "L010").code, ResultCode.NOT_ENROLLED)
        self.assertUnchanged(before)

    def test_unknown_learner_and_offering(self):
        before = self.service.snapshot()
        self.assertEqual(self.service.enroll(OFFERING, "L999").code, ResultCode.UNKNOWN_LEARNER)
        self.assertEqual(self.service.drop(OFFERING, "L999").code, ResultCode.UNKNOWN_LEARNER)
        self.assertEqual(self.service.enroll("XX999", "L003").code, ResultCode.UNKNOWN_OFFERING)
        self.assertEqual(self.service.drop("XX999", "L001").code, ResultCode.UNKNOWN_OFFERING)
        self.assertUnchanged(before)

    def test_malformed_ids(self):
        before = self.service.snapshot()
        for bad in [None, "", "   ", 42]:
            self.assertEqual(self.service.enroll(OFFERING, bad).code, ResultCode.INVALID_REQUEST)
            self.assertEqual(self.service.drop(OFFERING, bad).code, ResultCode.INVALID_REQUEST)
            self.assertEqual(self.service.enroll(bad, "L003").code, ResultCode.INVALID_REQUEST)
        self.assertUnchanged(before)

    def test_invalid_capacity_is_rejected_at_setup(self):
        with self.assertRaises(ValueError):
            InMemoryStore(Offering(OFFERING, "CS201", 0, None))


# the lock keeps capacity and duplicates correct
class ConcurrencyTests(BaseTest):
    def run_together(self, calls):
        """Start all calls at the same moment using a barrier, then wait."""
        barrier = threading.Barrier(len(calls))
        results = [None] * len(calls)

        def worker(index, fn):
            barrier.wait()
            results[index] = fn()

        threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(calls)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_many_learners_racing_for_few_seats(self):
        service, _ = make_service(capacity=3, passed=ALL)
        results = self.run_together([lambda lid=lid: service.enroll(OFFERING, lid) for lid in ALL])
        codes = [r.code for r in results]
        self.assertEqual(codes.count(ResultCode.ENROLLED), 3)
        self.assertEqual(codes.count(ResultCode.WAITLISTED), 17)
        self.assertEqual(len(service.snapshot()["waitlist"]), 17)
        self.assertConsistent(service)

    def test_same_learner_sending_many_requests_at_once(self):
        service, store = make_service(capacity=3, passed=ALL)
        results = self.run_together([lambda: service.enroll(OFFERING, "L001")] * 10)
        codes = [r.code for r in results]
        self.assertEqual(codes.count(ResultCode.ENROLLED), 1)
        self.assertEqual(codes.count(ResultCode.ALREADY_ENROLLED), 9)
        self.assertEqual(len(store.enrollments), 1)
        self.assertConsistent(service)

    def test_concurrent_repeated_drops_promote_only_once(self):
        service, _ = make_service(capacity=1, passed=ALL)
        for learner_id in ["L001", "L002", "L003"]:
            service.enroll(OFFERING, learner_id)
        results = self.run_together([lambda: service.drop(OFFERING, "L001")] * 8)
        codes = [r.code for r in results]
        self.assertEqual(codes.count(ResultCode.DROPPED), 1)
        self.assertEqual(codes.count(ResultCode.NOT_ENROLLED), 7)
        self.assertEqual(service.snapshot()["enrolled"], ["L002"])
        self.assertEqual(service.snapshot()["waitlist"], ["L003"])
        self.assertConsistent(service)


# Seed data used by the CLI produces the documented state
class SeedDataTests(BaseTest):
    def test_demo_seed_matches_sample_tables(self):
        from waitlist.cli import build_demo
        service, _ = build_demo()
        snap = service.snapshot()
        self.assertEqual(snap["enrolled"], ["L001", "L002", "L003"])
        self.assertEqual(snap["waitlist"], ["L005", "L004", "L006"])
        result = service.drop(OFFERING, "L001")
        self.assertEqual((result.skipped, result.promoted), (("L005",), ("L004",)))
        self.assertConsistent(service)


if __name__ == "__main__":
    unittest.main()
