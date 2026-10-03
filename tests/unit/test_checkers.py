import pytest

from adaptiveroute.evaluation.checkers import (
    check_output,
    extract_code,
    extract_number,
    extract_sql,
)
from adaptiveroute.evaluation.dataset import (
    ConstraintsCheck,
    ContainsCheck,
    NumericCheck,
    PythonCheck,
    SqlCheck,
)

# --- numeric ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("so the result is 42", 42.0),
        ("first 3, then 7.5", 7.5),
        ("Working: 2+2=4\nANSWER: 1,234.5", 1234.5),
        ("ANSWER: 3/4", 0.75),
        ("ANSWER = -2.5e3", -2500.0),
        ("the answer is \\boxed{17} (not 18 apples)", 17.0),
        ("ANSWER: $12.50", 12.5),
        ("no digits here", None),
    ],
)
def test_extract_number(text: str, expected: float | None) -> None:
    assert extract_number(text) == expected


def test_answer_line_wins_over_trailing_numbers() -> None:
    assert extract_number("ANSWER: 10\n(checked with 3 methods)") == 10.0


def test_numeric_tolerance() -> None:
    chk = NumericCheck(type="numeric", answer=0.3333, rel_tol=1e-3)
    assert check_output(chk, "ANSWER: 0.33333").passed
    assert not check_output(chk, "ANSWER: 0.34").passed


# --- python -----------------------------------------------------------------------

PY = PythonCheck(
    type="python",
    entrypoint="add",
    tests=["assert add(1, 2) == 3", "assert add(-1, 1) == 0", "assert add(0, 0) == 0"],
)


def test_python_passes_correct_code_in_fence() -> None:
    out = "Here you go:\n```python\ndef add(a, b):\n    return a + b\n```\nDone."
    assert check_output(PY, out).passed


def test_python_fails_wrong_code() -> None:
    res = check_output(PY, "```python\ndef add(a, b):\n    return a - b\n```")
    assert not res.passed
    assert "AssertionError" in res.detail


def test_python_requires_entrypoint() -> None:
    res = check_output(PY, "```python\ndef plus(a, b):\n    return a + b\n```")
    assert not res.passed and "no definition" in res.detail


def test_python_timeout() -> None:
    chk = PY.model_copy(update={"timeout_s": 1.0})
    res = check_output(chk, "```python\ndef add(a, b):\n    while True: pass\n```")
    assert not res.passed and ("timed out" in res.detail or "failed" in res.detail)


def test_python_child_has_no_inherited_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "secret-should-not-leak")
    chk = PythonCheck(
        type="python",
        entrypoint="f",
        tests=["import os", "assert 'GROQ_API_KEY' not in os.environ", "assert f() == 1"],
    )
    assert check_output(chk, "```python\ndef f():\n    return 1\n```").passed


def test_extract_code_prefers_block_defining_entrypoint() -> None:
    out = "```python\nprint('example')\n```\n```python\ndef target():\n    return 1\n```"
    assert "def target" in extract_code(out, "target")


# --- sql --------------------------------------------------------------------------

SQL = SqlCheck(type="sql", reference_sql="SELECT COUNT(*) FROM orders WHERE status = 'cancelled'")


def test_sql_equivalent_query_passes() -> None:
    out = "```sql\nSELECT count(id) AS n FROM orders WHERE status IN ('cancelled');\n```"
    assert check_output(SQL, out).passed


def test_sql_wrong_result_fails() -> None:
    out = "```sql\nSELECT COUNT(*) FROM orders\n```"
    assert not check_output(SQL, out).passed


def test_sql_syntax_error_fails_gracefully() -> None:
    res = check_output(SQL, "```sql\nSELEC nonsense\n```")
    assert not res.passed and "SQL error" in res.detail


def test_sql_cannot_modify_fixture() -> None:
    res = check_output(SQL, "```sql\nDELETE FROM orders\n```")
    assert not res.passed
    # and the reference still sees the original data afterwards
    ok = "```sql\nSELECT COUNT(*) FROM orders WHERE status = 'cancelled'\n```"
    assert check_output(SQL, ok).passed


def test_sql_order_sensitivity() -> None:
    ref = "SELECT name FROM products ORDER BY unit_price DESC LIMIT 3"
    reversed_sql = (
        "```sql\nSELECT name FROM (SELECT name, unit_price FROM products "
        "ORDER BY unit_price DESC LIMIT 3) ORDER BY unit_price ASC\n```"
    )
    assert check_output(SqlCheck(type="sql", reference_sql=ref), reversed_sql).passed
    strict = SqlCheck(type="sql", reference_sql=ref, order_matters=True)
    assert not check_output(strict, reversed_sql).passed


def test_extract_sql_bare_statement() -> None:
    assert extract_sql("Try: SELECT 1; it works") == "SELECT 1"
    assert extract_sql("no query") is None


# --- constraints --------------------------------------------------------------------


def test_constraints_all_met() -> None:
    chk = ConstraintsCheck(
        type="constraints", max_words=12, must_include=["Friday"], bullet_count=2
    )
    assert check_output(chk, "- Off on Friday\n- Back Monday").passed


def test_constraints_report_each_failure() -> None:
    chk = ConstraintsCheck(
        type="constraints", max_words=3, must_not_include=["sorry"], max_sentences=1
    )
    res = check_output(chk, "Sorry for the delay. It will ship soon.")
    assert not res.passed
    assert "words" in res.detail and "sorry" in res.detail and "sentences" in res.detail


def test_forbidden_word_uses_word_boundaries() -> None:
    chk = ConstraintsCheck(type="constraints", must_not_include=["cat"])
    assert check_output(chk, "Concatenate the strings.").passed


# --- contains -----------------------------------------------------------------------


def test_contains_aliases_and_accents() -> None:
    chk = ContainsCheck(type="contains", any_of=["Sao Paulo"])
    assert check_output(chk, "The largest city is São Paulo.").passed


def test_contains_whole_words_only() -> None:
    chk = ContainsCheck(type="contains", any_of=["W"])
    assert not check_output(chk, "Tungsten's symbol is weird").passed
    assert check_output(chk, "Its symbol is W.").passed


def test_contains_rejects_hedged_wrong_answers() -> None:
    chk = ContainsCheck(type="contains", any_of=["Canberra"], none_of=["Sydney"])
    assert not check_output(chk, "Either Sydney or Canberra.").passed


def test_empty_output_always_fails() -> None:
    assert not check_output(ContainsCheck(type="contains", any_of=["x"]), "  ").passed
    assert not check_output(ContainsCheck(type="contains", any_of=["x"]), None).passed
