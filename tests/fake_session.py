"""In-memory stand-in for a SQLAlchemy session, for route tests.

Supports the subset the routers actually use: query/filter/order_by/first/all/
count, add with auto-incrementing ids, delete, commit, refresh, rollback.
`matches` interprets real SQLAlchemy filter clauses against plain ORM objects,
so tests exercise the router's actual query expressions.

Shared by the diagnostic and practice-test route tests.
"""

from __future__ import annotations


class _FakeQuery:
    def __init__(self, session: "FakeSession", model, rows):
        self.session = session
        self.model = model
        self.rows = list(rows)

    def filter(self, *clauses):
        rows = self.rows
        for clause in clauses:
            rows = [row for row in rows if self.session.matches(row, clause)]
        return _FakeQuery(self.session, self.model, rows)

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)

    def count(self):
        return len(self.rows)

    def update(self, values, synchronize_session=False):
        """Set columns on every matched row, returning how many matched.

        The routes use this for writes that must not race -- the filter carries
        the state the write depends on -- so the count matters as much as the
        change: zero means somebody else got there first.
        """
        for row in self.rows:
            for column, value in values.items():
                name = getattr(column, "key", column)
                setattr(row, name, value)
        return len(self.rows)


class FakeSession:
    def __init__(self, models=()):
        self.store: dict = {model: [] for model in models}
        self.counters: dict = {model: 1 for model in models}
        self.committed = False

    def query(self, model):
        return _FakeQuery(self, model, self.store.get(model, []))

    def add(self, obj):
        model = type(obj)
        current_id = getattr(obj, "id", None)
        next_id = self.counters.get(model, 1)
        if current_id is None:
            obj.id = next_id
            self.counters[model] = next_id + 1
        elif isinstance(current_id, int):
            self.counters[model] = max(next_id, current_id + 1)
        bucket = self.store.setdefault(model, [])
        if obj not in bucket:
            bucket.append(obj)

    def delete(self, obj):
        bucket = self.store.get(type(obj), [])
        if obj in bucket:
            bucket.remove(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        return None

    def rollback(self):
        return None

    def matches(self, row, clause) -> bool:
        clauses = getattr(clause, "clauses", None)
        operator = getattr(clause, "operator", None)
        if clauses is not None and operator is not None:
            parts = [self.matches(row, child) for child in clauses]
            op_name = getattr(operator, "__name__", str(operator))
            if op_name in ("or_", "or"):
                return any(parts)
            return all(parts)

        left = getattr(clause, "left", None)
        right = getattr(clause, "right", None)
        key = getattr(left, "key", None)
        if key is None:
            return False
        actual = getattr(row, key, None)
        expected = right.value if hasattr(right, "value") else right
        op_name = getattr(operator, "__name__", "")
        if operator is not None and (
            op_name in ("in_op", "in_") or getattr(operator, "__name__", "") == "in_op"
        ):
            values = expected
            if isinstance(values, (list, tuple, set)):
                return actual in values
            return False
        if op_name in ("is_", "is"):
            name = type(expected).__name__ if expected is not None else "NoneType"
            if expected is None or name == "Null":
                return actual is None
            if name == "True_":
                return actual is True
            if name == "False_":
                return actual is False
            expected_value = expected.value if hasattr(expected, "value") else expected
            return actual is expected_value
        return actual == expected
