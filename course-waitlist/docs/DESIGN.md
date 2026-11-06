# Design Notes: Course Waitlist

## 1. Scope

One course offering (`CS201-2026F-01` in the demo) and three things: enroll, drop, and FIFO promotion from the waitlist. It's a small Python service with an in-memory store, driven by a CLI and the tests.

Not covered: frontend, course catalog, payments, identity, multi-term registration, deployment, any real database, configurable policies.

```
 CLI (waitlist/cli.py)    tests (tests/)
            \                /
        RegistrationService  (waitlist/service.py)   <- rules, lock, results
                   |
            InMemoryStore    (waitlist/store.py)     <- plain data, no rules
```

`RegistrationService` is the only thing that changes state. The store just holds data and has no rules or locking of its own.

## 2. Data model (`waitlist/store.py`)

| Record | Key fields | Notes |
|---|---|---|
| `Offering` | `offering_id`, `course_code`, `capacity` (>0), `prerequisite_course` (optional) | One per service |
| `Learner` | `learner_id`, `full_name` | Reference data only |
| course history | `(learner_id, course_code) -> PrereqStatus` | `PASSED`, `FAILED`, `IN_PROGRESS`, `REVOKED` |
| `Enrollment` | `enrollment_id`, `learner_id`, `status` (`ENROLLED`/`DROPPED`), `source` (`DIRECT`/`WAITLIST_PROMOTION`), `enrolled_seq`, `dropped_seq` | Never deleted, so there's a history |
| `WaitlistEntry` | `entry_id`, `learner_id`, `status` (`WAITING`/`PROMOTED`/`SKIPPED`), `joined_seq`, `resolved_seq`, `note` | Never deleted |

**Ordering:** every write takes the next number from a counter (`store.tick()`) and the queue is sorted by `joined_seq`. I used a counter instead of timestamps because two requests can get the same timestamp, and then FIFO order would be ambiguous.

`docs/course_waitlist_sample.sql` is the same model as SQL tables. It's reference only and isn't used at runtime.

## 3. API

Both calls take `(offering_id, learner_id)` and always return a `Result`. They don't raise for bad requests.

```python
Result(code, message, learner_id, waitlist_position=None, promoted=(), skipped=())
result.ok  # True for ENROLLED, WAITLISTED, DROPPED
```

| Call | Result codes |
|---|---|
| `enroll` | `ENROLLED`, `WAITLISTED`, `ALREADY_ENROLLED`, `ALREADY_WAITLISTED`, `PREREQUISITE_NOT_MET` |
| `drop` | `DROPPED` (with `promoted` / `skipped`), `NOT_ENROLLED` (message says why: already dropped, on the waitlist, or never enrolled) |
| both | `INVALID_REQUEST` (empty or non-string id), `UNKNOWN_OFFERING`, `UNKNOWN_LEARNER` |

Anything other than a success code leaves the state untouched.

There are also two read-only helpers, `snapshot()` and `check_invariants()`.

### Order of checks in `enroll`

1. Validate the input.
2. Already enrolled? `ALREADY_ENROLLED`.
3. Already waiting? `ALREADY_WAITLISTED`.
4. Prerequisite not passed? `PREREQUISITE_NOT_MET` (they don't get waitlisted either).
5. Seat free? Enroll them.
6. Otherwise add them to the back of the waitlist.

Duplicates are checked before eligibility so someone who's already enrolled gets the clearer answer, even if their prerequisite was revoked after they enrolled.

## 4. Invariants

`check_invariants()` looks for these, and the tests assert it returns `[]` after state-changing scenarios:

1. Enrolled count never goes above capacity.
2. A learner has at most one `ENROLLED` row.
3. A learner has at most one `WAITING` entry.
4. Nobody is both enrolled and waiting.
5. If a seat is free, the waitlist is empty. `enroll` only waitlists when the course is full, and `drop` promotes until seats are full or the queue runs out, so a direct enroll can never jump the queue.

## 5. Prerequisite and waitlist policy

- **Prerequisite rule:** the learner must have `PASSED` the offering's prerequisite. `FAILED`, `IN_PROGRESS`, `REVOKED` and no record all fail. An offering with no prerequisite accepts any known learner.
- **When it's checked:** at enroll, and again when the learner reaches the front of the queue. The second check is there because a pass can be revoked while someone waits.
- **Skip, not retain:** if the learner at the front is ineligible when a seat opens, their entry becomes `SKIPPED`, they leave the queue, and promotion moves on to the next person. They can enroll again later and go to the back. A skip is never undone automatically.
  - *Why skip:* if an ineligible learner stayed at the front, either the seat would sit empty or every drop would need special handling to step over them. Skipping keeps the queue simple and the seat gets filled.
- **Only the front is checked.** Ineligible learners further back aren't removed early. They're dealt with when they reach the front.
- **One active record per learner.** I read "enroll only once" as one active enrollment at a time. Re-enrolling after a drop is allowed and treated as a new request.
- **Drop only applies to enrolled learners.** A waitlisted learner calling drop gets `NOT_ENROLLED`. Leaving the waitlist isn't part of this.
- **Promotion is immediate.** No offer/accept step.

## 6. Concurrency and persistence

- One `threading.Lock` in `RegistrationService` covers each whole call, from validation to the last write. That makes every call behave like a serialized transaction, so the seat check, duplicate check and promotion can't interleave between requests.
- One lock is enough here: one offering, in memory, one process. I considered a per-row lock or an optimistic `version` column and decided they were overkill without a real database.
- The concurrency tests release 10-20 threads at once with a barrier. `tools/mutation_check_lock.py` confirms those tests fail when the lock is removed.
- **Persistence:** none. State lives for one process run.

## 7. Trade-offs

| Decision | Upside | Downside |
|---|---|---|
| Single coarse lock | Simple and clearly correct | Every call is serialized (fine for one offering) |
| Linear scans in the store | Easy to read | O(n) per call, fine for a class |
| Counter for ordering | Deterministic FIFO, easy to test | Not a real timestamp |
| Results instead of exceptions | Callers always get a clear outcome | Caller has to check `result.ok` |
