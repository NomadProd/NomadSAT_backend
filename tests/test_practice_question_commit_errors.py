"""A rejected question row is bad input (4xx), not a server fault (500).

pg8000 maps only SQLSTATE 23505 to IntegrityError, so a check violation arrives
as ProgrammingError -- _commit_question branches on the SQLSTATE class instead.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DatabaseError

from routes.practice_tests import _commit_question


class FakeOrig(Exception):
    def __init__(self, info):
        super().__init__(info)


class FakeDb:
    def __init__(self, error):
        self.error = error
        self.rolled_back = False

    def commit(self):
        if self.error is not None:
            raise self.error

    def rollback(self):
        self.rolled_back = True


def db_raising(info):
    return FakeDb(DatabaseError("INSERT ...", {}, FakeOrig(info)))


def test_check_violation_becomes_422_naming_the_constraint():
    db = db_raising(
        {"C": "23514", "n": "ck_practice_test_questions_answer_shape"}
    )
    with pytest.raises(HTTPException) as excinfo:
        _commit_question(db)
    assert excinfo.value.status_code == 422
    assert "ck_practice_test_questions_answer_shape" in excinfo.value.detail
    assert db.rolled_back


def test_duplicate_order_index_becomes_409():
    db = db_raising({"C": "23505", "n": "uq_practice_test_questions_order"})
    with pytest.raises(HTTPException) as excinfo:
        _commit_question(db)
    assert excinfo.value.status_code == 409


def test_non_integrity_error_is_not_swallowed():
    db = db_raising({"C": "42601", "M": "syntax error"})
    with pytest.raises(DatabaseError):
        _commit_question(db)
    assert db.rolled_back


def test_unparseable_error_is_not_swallowed():
    db = FakeDb(DatabaseError("INSERT ...", {}, FakeOrig("not a dict")))
    with pytest.raises(DatabaseError):
        _commit_question(db)


def test_clean_commit_passes_through():
    db = FakeDb(None)
    _commit_question(db)
    assert not db.rolled_back
