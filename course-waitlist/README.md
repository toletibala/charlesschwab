# Course Waitlist

A small Python service for one course offering. A learner can enroll, drop, and get promoted off a FIFO waitlist when a seat opens. Prerequisites are checked when someone enrolls and again when they reach the front of the queue.

I kept it simple on purpose: everything is in memory and there's a tiny CLI for trying it out. No database, no frontend.

## Setup

Python 3.8+, standard library only, so there's nothing to install.

```bash
cd course-waitlist
```

## Running the tests

```bash
python3 -m unittest discover -s tests -v      # all tests should pass (OK)
python3 tools/mutation_check_lock.py          # should print: failures without lock: 3 of 3
```

`pytest -q` works too if you have it. (Use `python` instead of `python3` if that is how Python is named on your machine.)

The second command swaps the lock for a no-op and reruns the concurrency tests. They should fail, otherwise they aren't really testing the lock.

## Trying it by hand

```bash
python3 -m waitlist.cli demo    # scripted walkthrough
python3 -m waitlist.cli         # interactive: enroll L010 | drop L001 | status | quit
```

Demo data lives in `waitlist/cli.py`: offering `CS201-2026F-01`, capacity 3, requires CS101.

- Enrolled: L001, L002, L003
- Waitlist (in order): L005, L004, L006. L005's CS101 pass has been revoked.
- L007 failed CS101, L008 is still taking it, L009 has no record.

Try `drop L001`: L005 gets skipped and L004 gets the seat.

## Layout

```
waitlist/
  store.py     records, enums and the in-memory store (no rules)
  service.py   RegistrationService: rules, lock, results   <- start here
  cli.py       command-line prototype and its demo data
tests/test_service.py          the tests
tools/mutation_check_lock.py   checks the concurrency tests depend on the lock
docs/DESIGN.md                 design notes
```

## Rules I went with

- The prerequisite must be `PASSED`. Failed, in progress, revoked and "no record" all count as not eligible.
- If the person at the front of the waitlist is no longer eligible, they're skipped and removed from the queue, and the next person is tried. If they want back in they have to enroll again and go to the back.
- Only the front of the queue gets checked. Someone ineligible further back stays put until they reach the front.
- A learner has one active record at a time (enrolled or waiting). Repeating a request changes nothing.

More detail and the reasoning is in `docs/DESIGN.md`. Which parts used AI, the prompts, and what was accepted or rejected are in `docs/AI_USAGE.md`.

## Tests

| Area | Test class |
|---|---|
| Enrolling with seats free | `AvailableCapacityTests` |
| Full course, duplicate requests | `FullCapacityAndDuplicateTests` |
| FIFO promotion | `FifoPromotionTests` |
| Prerequisite failures, skip rule | `PrerequisiteTests` |
| Bad input, repeated drops | `InvalidAndRepeatedDropTests` |
| Concurrency | `ConcurrencyTests` (plus the lock check above) |
| Demo data | `SeedDataTests` |

## Known limitations

1. **No persistence.** State is lost when the process exits. A real version would do the same check-and-write steps inside one database transaction, with unique constraints for "one active record" (see `docs/course_waitlist_sample.sql`).
2. **The lock only works inside one process.** Multiple app instances would need database-level locking, e.g. `SELECT ... FOR UPDATE` or a conditional update.
3. **You can't leave the waitlist.** Calling drop while waitlisted returns `NOT_ENROLLED`. There's no withdraw operation.
4. **Promotion is immediate.** No offer/accept step and no notification, so a promoted learner might not know they have a seat.
5. **Skips are silent and permanent.** The result says who was skipped, but nobody is told. If their pass is restored later they have to enroll again and go to the back.
6. **Ineligible learners further back stay in the queue** until they reach the front, so queue positions can look better than they really are.
7. **Store lookups are linear scans.** Fine for a class, not for a huge queue.
8. **One coarse lock for everything**, including reads like `snapshot()`. Fine for a single offering.
9. **Prerequisite data is not protected by the lock.** The service reads a learner's course status at enroll and promotion time, but changes to that status (for example a revoked pass) are made directly on the store, outside the lock. The tests and demo do this on purpose; a real system would own that data in the same transaction or treat it as read-only input.
