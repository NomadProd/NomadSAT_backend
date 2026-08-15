from types import SimpleNamespace

from mock_assignments import (
    ensure_mock_assignments_for_session,
    ensure_mock_assignments_for_student,
)
from models import Assignment, ClassEnrollment, Session as ClassSession


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeDb:
    def __init__(self, payloads):
        self.payloads = {
            model: list(rows) if isinstance(rows, list) else [rows]
            for model, rows in payloads.items()
        }
        self.added = []

    def query(self, model):
        queue = self.payloads.get(model, [])
        if not queue:
            return _FakeQuery([])
        payload = queue.pop(0)
        self.payloads[model] = queue
        if isinstance(payload, list):
            return _FakeQuery(payload)
        return _FakeQuery([payload])

    def add(self, obj):
        self.added.append(obj)


def test_non_mock_session_creates_nothing():
    session = SimpleNamespace(session_type="verbal", id=1, class_id=10)
    created = ensure_mock_assignments_for_session(_FakeDb({}), session)
    assert created == 0


def test_mock_session_creates_assignment_for_missing_student():
    session = SimpleNamespace(
        session_type="mock",
        id=7,
        class_id=10,
        date=None,
        end_time=None,
    )
    db = _FakeDb({Assignment: []})
    created = ensure_mock_assignments_for_session(
        db, session, student_ids=[501]
    )
    assert created == 1
    assert len(db.added) == 1
    assert db.added[0].student_id == 501
    assert db.added[0].session_id == 7
    assert db.added[0].title == "Mock submission"


def test_mock_session_skips_existing_assignment():
    session = SimpleNamespace(
        session_type="mock",
        id=7,
        class_id=10,
        date=None,
        end_time=None,
    )
    existing = SimpleNamespace(id=41, session_id=7, student_id=501)
    db = _FakeDb({Assignment: [existing]})
    created = ensure_mock_assignments_for_session(
        db, session, student_ids=[501]
    )
    assert created == 0
    assert db.added == []


def test_ensure_for_student_only_targets_that_student():
    mock_session = SimpleNamespace(
        session_type="mock",
        id=7,
        class_id=10,
        date=None,
        end_time=None,
    )
    db = _FakeDb(
        {
            ClassSession: [mock_session],
            Assignment: [],
        }
    )
    created = ensure_mock_assignments_for_student(db, class_id=10, student_id=501)
    assert created == 1
    assert [row.student_id for row in db.added] == [501]
