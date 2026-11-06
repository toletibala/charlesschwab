"""Enroll / drop / waitlist rules for one course offering.

Rules, in short:
- You need PASSED on the prerequisite course to enroll or to be promoted.
- Eligibility is checked at enroll time and again when you reach the front
  of the waitlist (a pass can be revoked while you wait).
- If the person at the front is no longer eligible they are skipped and
  dropped from the queue for good; we move on to the next one.
- Only people at the front get checked, nobody behind them.
- One active record per learner: enrolled, waiting, or neither.

Everything runs under one lock, so two requests can't grab the last seat.
"""

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from .store import (EnrollmentSource, EnrollmentStatus, InMemoryStore,
                    PrereqStatus, WaitlistStatus)


class ResultCode(str, Enum):
    ENROLLED = "ENROLLED"
    WAITLISTED = "WAITLISTED"
    DROPPED = "DROPPED"
    ALREADY_ENROLLED = "ALREADY_ENROLLED"
    ALREADY_WAITLISTED = "ALREADY_WAITLISTED"
    PREREQUISITE_NOT_MET = "PREREQUISITE_NOT_MET"
    NOT_ENROLLED = "NOT_ENROLLED"
    UNKNOWN_LEARNER = "UNKNOWN_LEARNER"
    UNKNOWN_OFFERING = "UNKNOWN_OFFERING"
    INVALID_REQUEST = "INVALID_REQUEST"


SUCCESS_CODES = {ResultCode.ENROLLED, ResultCode.WAITLISTED, ResultCode.DROPPED}


@dataclass(frozen=True)
class Result:
    code: ResultCode
    message: str
    learner_id: Optional[str] = None
    waitlist_position: Optional[int] = None
    promoted: Tuple[str, ...] = field(default_factory=tuple)
    skipped: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.code in SUCCESS_CODES


class RegistrationService:
    def __init__(self, store: InMemoryStore):
        self._store = store
        self._lock = threading.Lock()

    def enroll(self, offering_id: str, learner_id: str) -> Result:
        with self._lock:
            error = self._validate(offering_id, learner_id)
            if error:
                return error

            # duplicates first, so a repeat request never creates anything
            if self._store.active_enrollment(learner_id):
                return Result(ResultCode.ALREADY_ENROLLED,
                              f"{learner_id} is already enrolled; nothing changed.", learner_id)
            if self._store.waiting_entry(learner_id):
                position = self._position_of(learner_id)
                return Result(ResultCode.ALREADY_WAITLISTED,
                              f"{learner_id} is already on the waitlist at position {position}; "
                              "nothing changed.", learner_id, waitlist_position=position)

            if not self._is_eligible(learner_id):
                return Result(ResultCode.PREREQUISITE_NOT_MET,
                              f"{learner_id} has not passed {self._store.offering.prerequisite_course}.",
                              learner_id)

            # a free seat means the queue is empty, so nobody gets jumped
            if self._seats_available() > 0:
                self._store.add_enrollment(learner_id, EnrollmentSource.DIRECT)
                return Result(ResultCode.ENROLLED, f"{learner_id} enrolled.", learner_id)

            self._store.add_waitlist_entry(learner_id)
            position = self._position_of(learner_id)
            return Result(ResultCode.WAITLISTED,
                          f"Course is full; {learner_id} is on the waitlist at position {position}.",
                          learner_id, waitlist_position=position)

    def drop(self, offering_id: str, learner_id: str) -> Result:
        with self._lock:
            error = self._validate(offering_id, learner_id)
            if error:
                return error

            enrollment = self._store.active_enrollment(learner_id)
            if enrollment is None:
                if self._store.waiting_entry(learner_id):
                    reason = "is on the waitlist, not enrolled"
                elif self._store.has_dropped_before(learner_id):
                    reason = "has already dropped"
                else:
                    reason = "is not enrolled"
                return Result(ResultCode.NOT_ENROLLED,
                              f"{learner_id} {reason}; nothing changed.", learner_id)

            enrollment.status = EnrollmentStatus.DROPPED
            enrollment.dropped_seq = self._store.tick()

            promoted, skipped = self._promote_from_waitlist()
            message = f"{learner_id} dropped."
            if skipped:
                message += f" Skipped (prerequisite not met): {', '.join(skipped)}."
            if promoted:
                message += f" Promoted from waitlist: {', '.join(promoted)}."
            return Result(ResultCode.DROPPED, message, learner_id,
                          promoted=tuple(promoted), skipped=tuple(skipped))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "offering_id": self._store.offering.offering_id,
                "capacity": self._store.offering.capacity,
                "enrolled": self._enrolled_ids(),
                "seats_available": self._seats_available(),
                "waitlist": [e.learner_id for e in self._store.waiting_entries()],
            }

    def check_invariants(self) -> List[str]:
        """Returns a list of problems; empty means everything is consistent."""
        with self._lock:
            problems = []
            capacity = self._store.offering.capacity
            enrolled = self._enrolled_ids()
            waiting = [e.learner_id for e in self._store.waiting_entries()]

            if len(enrolled) > capacity:
                problems.append(f"over capacity: {len(enrolled)} > {capacity}")
            if len(enrolled) != len(set(enrolled)):
                problems.append("a learner has more than one active enrollment")
            if len(waiting) != len(set(waiting)):
                problems.append("a learner has more than one waiting entry")
            both = set(enrolled) & set(waiting)
            if both:
                problems.append(f"learners both enrolled and waiting: {sorted(both)}")
            if waiting and len(enrolled) < capacity:
                problems.append("a seat is free while learners are still waiting")
            return problems

    # --- helpers (caller already holds the lock) ---

    def _validate(self, offering_id, learner_id) -> Optional[Result]:
        if not isinstance(offering_id, str) or not offering_id.strip():
            return Result(ResultCode.INVALID_REQUEST, "offering_id must be a non-empty string.")
        if not isinstance(learner_id, str) or not learner_id.strip():
            return Result(ResultCode.INVALID_REQUEST, "learner_id must be a non-empty string.")
        if offering_id != self._store.offering.offering_id:
            return Result(ResultCode.UNKNOWN_OFFERING,
                          f"Unknown offering '{offering_id}'. This service handles "
                          f"'{self._store.offering.offering_id}' only.", learner_id)
        if self._store.get_learner(learner_id) is None:
            return Result(ResultCode.UNKNOWN_LEARNER, f"Unknown learner '{learner_id}'.", learner_id)
        return None

    def _enrolled_ids(self) -> List[str]:
        return [r.learner_id for r in self._store.enrollments
                if r.status == EnrollmentStatus.ENROLLED]

    def _is_eligible(self, learner_id: str) -> bool:
        prereq = self._store.offering.prerequisite_course
        if prereq is None:
            return True
        return self._store.get_course_status(learner_id, prereq) == PrereqStatus.PASSED

    def _seats_available(self) -> int:
        return self._store.offering.capacity - len(self._enrolled_ids())

    def _position_of(self, learner_id: str) -> Optional[int]:
        for index, entry in enumerate(self._store.waiting_entries(), start=1):
            if entry.learner_id == learner_id:
                return index
        return None

    def _promote_from_waitlist(self) -> Tuple[List[str], List[str]]:
        """Walk the queue front to back while there are free seats.
        Eligible -> enrolled. Ineligible -> skipped and removed."""
        promoted: List[str] = []
        skipped: List[str] = []
        for entry in self._store.waiting_entries():
            if self._seats_available() <= 0:
                break
            entry.resolved_seq = self._store.tick()
            if self._is_eligible(entry.learner_id):
                entry.status = WaitlistStatus.PROMOTED
                entry.note = "Promoted after a seat opened"
                self._store.add_enrollment(entry.learner_id, EnrollmentSource.WAITLIST_PROMOTION)
                promoted.append(entry.learner_id)
            else:
                entry.status = WaitlistStatus.SKIPPED
                entry.note = "Prerequisite not met when reaching the front of the queue"
                skipped.append(entry.learner_id)
        return promoted, skipped
