import sys
from .service import RegistrationService
from .store import InMemoryStore, Learner, Offering, PrereqStatus

# demo data: CS201-2026F-01, capacity 3, needs CS101
# starts with L001-L003 enrolled and L005, L004, L006 waiting

OFFERING_ID = "CS201-2026F-01"

LEARNERS = [
    ("L001", "Ronaldo"),
    ("L002", "Leo Messi"),
    ("L003", "MS Dhoni"),
    ("L004", "Virat kohli"),
    ("L005", "Hrithik Roshan"),
    ("L006", "Ananya"),
    ("L007", "Karthik"),
    ("L008", "Hemant"),
    ("L009", "Jayant Toleti"), 
    ("L010", "Bala Toleti"),
]

CS101_HISTORY = {
    "L001": PrereqStatus.PASSED,
    "L002": PrereqStatus.PASSED,
    "L003": PrereqStatus.PASSED,
    "L004": PrereqStatus.PASSED,
    "L005": PrereqStatus.PASSED,  
    "L006": PrereqStatus.PASSED,
    "L007": PrereqStatus.FAILED,
    "L008": PrereqStatus.IN_PROGRESS,
    "L010": PrereqStatus.PASSED,
}


def build_demo() -> tuple:
    # go through enroll() instead of writing rows, so the demo follows the real rules
    store = InMemoryStore(Offering(OFFERING_ID, "CS201", capacity=3, prerequisite_course="CS101"))
    for learner_id, name in LEARNERS:
        store.add_learner(Learner(learner_id, name))
    for learner_id, status in CS101_HISTORY.items():
        store.set_course_status(learner_id, "CS101", status)

    service = RegistrationService(store)
    for learner_id in ["L001", "L002", "L003", "L005", "L004", "L006"]:
        service.enroll(OFFERING_ID, learner_id)

    # L005's CS101 pass is withdrawn while they wait; they will be skipped.
    store.set_course_status("L005", "CS101", PrereqStatus.REVOKED)
    return service, store


def print_status(service) -> None:
    snap = service.snapshot()
    print(f"  {snap['offering_id']}  capacity={snap['capacity']}  "
          f"seats_available={snap['seats_available']}")
    print(f"  enrolled : {', '.join(snap['enrolled']) or '-'}")
    print(f"  waitlist : {', '.join(snap['waitlist']) or '-'}")


def run_command(service, line: str) -> bool:
    parts = line.split()
    if not parts:
        return True
    command, args = parts[0].lower(), parts[1:]

    if command in ("quit", "exit"):
        return False
    if command == "status":
        print_status(service)
    elif command in ("enroll", "drop") and len(args) == 1:
        action = service.enroll if command == "enroll" else service.drop
        result = action(OFFERING_ID, args[0])
        print(f"  [{result.code.value}] {result.message}")
    else:
        print("  commands: enroll <id> | drop <id> | status | quit")
    return True


DEMO_SCRIPT = [
    "status",
    "enroll L004",   
    "enroll L001",   
    "enroll L007",   
    "enroll L009",   
    "enroll L010",   
    "drop L001",    
    "status",
    "drop L001",     
    "drop L999",     
    "status",
]


def main(argv) -> int:
    service, _ = build_demo()
    if argv and argv[0] == "demo":
        for line in DEMO_SCRIPT:
            print(f"> {line}")
            run_command(service, line)
        return 0

    print("Course waitlist CLI. Type 'status', 'enroll <id>', 'drop <id>' or 'quit'.")
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if not run_command(service, line):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
