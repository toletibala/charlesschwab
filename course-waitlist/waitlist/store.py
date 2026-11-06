"""Records and the in-memory store for one course offering.

The store only holds data. Rules and locking live in RegistrationService.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


class PrereqStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"
    REVOKED = "REVOKED"


class EnrollmentStatus(str, Enum):
    ENROLLED = "ENROLLED"
    DROPPED = "DROPPED"


class EnrollmentSource(str, Enum):
    DIRECT = "DIRECT"
    WAITLIST_PROMOTION = "WAITLIST_PROMOTION"


class WaitlistStatus(str, Enum):
    WAITING = "WAITING"
    PROMOTED = "PROMOTED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class Learner:
    learner_id: str
    full_name: str


@dataclass(frozen=True)
class Offering:
    offering_id: str
    course_code: str
    capacity: int
    prerequisite_course: Optional[str] = None


@dataclass
class Enrollment:
    enrollment_id: int
    learner_id: str
    status: EnrollmentStatus
    source: EnrollmentSource
    enrolled_seq: int
    dropped_seq: Optional[int] = None


@dataclass
class WaitlistEntry:
    entry_id: int
    learner_id: str
    status: WaitlistStatus
    joined_seq: int
    resolved_seq: Optional[int] = None
    note: Optional[str] = None


class InMemoryStore:
    def __init__(self, offering: Offering):
        if offering.capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.offering = offering
        self.learners: Dict[str, Learner] = {}
        self.course_history: Dict[Tuple[str, str], PrereqStatus] = {}
        # rows are never deleted, so we keep the history of drops/promotions
        self.enrollments: List[Enrollment] = []
        self.waitlist: List[WaitlistEntry] = []
        self._next_id = 1
        # a counter instead of timestamps: two requests can share a timestamp,
        # but never a counter value, so FIFO order is always clear
        self._clock = 0

    def next_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def tick(self) -> int:
        self._clock += 1
        return self._clock

    def add_learner(self, learner: Learner) -> None:
        self.learners[learner.learner_id] = learner

    def get_learner(self, learner_id: str) -> Optional[Learner]:
        return self.learners.get(learner_id)

    def set_course_status(self, learner_id: str, course_code: str, status: PrereqStatus) -> None:
        self.course_history[(learner_id, course_code)] = status

    def get_course_status(self, learner_id: str, course_code: str) -> Optional[PrereqStatus]:
        return self.course_history.get((learner_id, course_code))

    def active_enrollment(self, learner_id: str) -> Optional[Enrollment]:
        for row in self.enrollments:
            if row.learner_id == learner_id and row.status == EnrollmentStatus.ENROLLED:
                return row
        return None

    def has_dropped_before(self, learner_id: str) -> bool:
        return any(row.learner_id == learner_id and row.status == EnrollmentStatus.DROPPED
                   for row in self.enrollments)

    def add_enrollment(self, learner_id: str, source: EnrollmentSource) -> Enrollment:
        row = Enrollment(self.next_id(), learner_id, EnrollmentStatus.ENROLLED,
                         source, enrolled_seq=self.tick())
        self.enrollments.append(row)
        return row

    def waiting_entry(self, learner_id: str) -> Optional[WaitlistEntry]:
        for entry in self.waitlist:
            if entry.learner_id == learner_id and entry.status == WaitlistStatus.WAITING:
                return entry
        return None

    def waiting_entries(self) -> List[WaitlistEntry]:
        """Everyone still waiting, earliest first."""
        waiting = [e for e in self.waitlist if e.status == WaitlistStatus.WAITING]
        return sorted(waiting, key=lambda e: e.joined_seq)

    def add_waitlist_entry(self, learner_id: str) -> WaitlistEntry:
        entry = WaitlistEntry(self.next_id(), learner_id, WaitlistStatus.WAITING,
                              joined_seq=self.tick())
        self.waitlist.append(entry)
        return entry
