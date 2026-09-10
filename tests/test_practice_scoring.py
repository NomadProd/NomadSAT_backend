import pytest

from services.practice_test_scoring import scaled_score, spr_is_correct, total_score


# --- scaled_score -----------------------------------------------------------

@pytest.mark.parametrize(
    "raw, max_raw, expected",
    [
        (0, 27, 200),
        (27, 27, 800),
        (22, 22, 800),
        (0, 0, 200),          # empty module never divides by zero
        (30, 27, 800),        # clamped, never above the ceiling
        (-1, 27, 200),        # clamped, never below the floor
    ],
)
def test_scaled_score_endpoints(raw, max_raw, expected):
    assert scaled_score(raw, max_raw) == expected


def test_scaled_score_is_monotonic():
    scores = [scaled_score(raw, 27) for raw in range(28)]
    assert scores == sorted(scores)


def test_scaled_score_honours_a_lower_ceiling():
    # The adaptive easy path will pass a lower ceiling; nothing else changes.
    assert scaled_score(27, 27, ceiling=600) == 600
    assert scaled_score(0, 27, ceiling=600) == 200


def test_total_score_is_clamped():
    assert total_score(800, 800) == 1600
    assert total_score(200, 200) == 400


# --- spr_is_correct ---------------------------------------------------------

@pytest.mark.parametrize(
    "response",
    ["1/2", "2/4", "4/8", "0.5", ".5", "0.50"],
)
def test_unreduced_fractions_and_equivalent_decimals(response):
    assert spr_is_correct(response, ["1/2"]) is True


@pytest.mark.parametrize(
    "response, expected",
    [
        ("2/3", True),
        (".6666", True),    # truncated, fills the field
        (".6667", True),    # rounded, fills the field
        ("0.666", True),
        ("0.667", True),
        (".666", False),    # does not fill the field
        ("0.66", False),    # explicitly rejected by College Board
        ("0.7", False),
        (".6665", False),   # fills the field but is not the right value
    ],
)
def test_repeating_decimal_must_fill_the_field(response, expected):
    assert spr_is_correct(response, ["2/3"]) is expected


def test_negative_answers_use_the_six_character_field():
    assert spr_is_correct("-1/2", ["-1/2"]) is True
    assert spr_is_correct("-0.5", ["-1/2"]) is True
    assert spr_is_correct("-.6666", ["-2/3"]) is True
    assert spr_is_correct("0.5", ["-1/2"]) is False


@pytest.mark.parametrize(
    "response",
    [
        "3 1/2",     # mixed number
        "50%",
        "$5",
        "1,000",
        "1/0",
        ".",
        "1..2",
        "1/2/3",
        "abc",
        "",
        "   ",
        "1/-2",
        "+5",
    ],
)
def test_unenterable_input_is_rejected_without_raising(response):
    assert spr_is_correct(response, ["1/2", "0.5", "3.5", "5", "1000"]) is False


def test_entry_longer_than_the_field_is_rejected():
    assert spr_is_correct("0.66667", ["2/3"]) is False       # 7 chars
    assert spr_is_correct("-0.66667", ["-2/3"]) is False     # 8 chars


def test_any_one_of_several_accepted_answers_matches():
    accepted = ["1/2", "3/4"]
    assert spr_is_correct("0.75", accepted) is True
    assert spr_is_correct("2/4", accepted) is True
    assert spr_is_correct("0.6", accepted) is False


def test_unparseable_stored_answer_is_skipped_not_fatal():
    assert spr_is_correct("1/2", ["garbage", "0.5"]) is True
    assert spr_is_correct("1/2", ["garbage"]) is False


def test_empty_answer_list_is_never_correct():
    assert spr_is_correct("1/2", []) is False
    assert spr_is_correct("1/2", None) is False


def test_integers_and_whitespace_padding():
    assert spr_is_correct("5", ["5"]) is True
    assert spr_is_correct(" 5 ", ["5"]) is True
    assert spr_is_correct("5.0", ["5"]) is True
    assert spr_is_correct("6", ["5"]) is False
