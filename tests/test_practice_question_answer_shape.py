"""ck_practice_test_questions_answer_shape compares against SQL NULL.

An mcq question must store correct_answers IS NULL and an spr question
choices IS NULL. SQLAlchemy's JSONB defaults to none_as_null=False, which
writes JSON 'null' instead -- a value that is NOT NULL, so every insert and
update is rejected by the constraint.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import dialect

from models import PracticeTestQuestion


def test_answer_shape_jsonb_columns_bind_none_as_sql_null():
    for name in ("choices", "correct_answers"):
        column_type = PracticeTestQuestion.__table__.c[name].type
        bound = column_type.bind_processor(dialect())(None)
        assert bound is None, (
            f"{name} binds Python None as {bound!r}; the CHECK constraint tests "
            "IS NULL, which JSON null fails"
        )
