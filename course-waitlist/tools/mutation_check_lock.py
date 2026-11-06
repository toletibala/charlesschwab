"""Verification helper, not part of the feature.

Swaps the service lock for a no-op and widens the race window, then runs the
concurrency tests. They should FAIL; if they passed without the lock, they
would prove nothing about the lock.

Run from the project root:  python tools/mutation_check_lock.py
Expected last line:          failures without lock: 3 of 3
"""

import contextlib
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import waitlist.service as svc  # noqa: E402
from waitlist.store import InMemoryStore  # noqa: E402

_orig_init = svc.RegistrationService.__init__


def _no_lock_init(self, store):
    _orig_init(self, store)
    self._lock = contextlib.nullcontext()


_orig_seats = svc.RegistrationService._seats_available


def _slow_seats(self):
    value = _orig_seats(self)
    time.sleep(0.001)  # widen the gap between "check" and "write"
    return value


_orig_active = InMemoryStore.active_enrollment


def _slow_active(self, learner_id):
    value = _orig_active(self, learner_id)
    time.sleep(0.001)
    return value


svc.RegistrationService.__init__ = _no_lock_init
svc.RegistrationService._seats_available = _slow_seats
InMemoryStore.active_enrollment = _slow_active

from tests import test_service  # noqa: E402

suite = unittest.TestLoader().loadTestsFromTestCase(test_service.ConcurrencyTests)
result = unittest.TextTestRunner(verbosity=1).run(suite)
print("failures without lock:", len(result.failures) + len(result.errors), "of", result.testsRun)
