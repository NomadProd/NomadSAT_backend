from types import SimpleNamespace

from routers.classes_router import load_plan_items_by_id, serialize_session


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class _CountingDb:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def query(self, model):
        self.queries += 1
        return _FakeQuery(self.rows)


def _plan_item(plan_item_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=plan_item_id,
        subject="verbal",
        general_topic=f"topic-{plan_item_id}",
        plan_text=f"plan-{plan_item_id}",
    )


def test_load_plan_items_by_id_queries_once_for_all_sessions():
    sessions = [
        SimpleNamespace(academic_plan_item_id=[1, 2]),
        SimpleNamespace(academic_plan_item_id=[2, 3]),
        SimpleNamespace(academic_plan_item_id=None),
    ]
    items = [_plan_item(1), _plan_item(2), _plan_item(3)]
    db = _CountingDb(items)

    by_id = load_plan_items_by_id(sessions, db)

    assert db.queries == 1
    assert set(by_id) == {1, 2, 3}


def test_serialize_session_uses_prefetched_plan_items_without_querying():
    session = SimpleNamespace(
        id=10,
        class_id=1,
        teacher_id=2,
        date=None,
        start_time=None,
        end_time=None,
        session_type="verbal",
        subject=None,
        topic="Reading",
        academic_plan_item_id=[1],
        lesson_notes=None,
        mock_document=None,
    )
    plan_item = _plan_item(1)
    db = _CountingDb([plan_item])

    payload = serialize_session(session, db, {1: plan_item})

    assert db.queries == 0
    assert payload["academic_plan_item_ids"] == [1]
    assert payload["academic_plan_items"] == [
        {
            "id": 1,
            "subject": "verbal",
            "general_topic": "topic-1",
            "plan_text": "plan-1",
        }
    ]
