"""Deterministic task-success checkers.

Checkers look only at the agent's output text, never at which agent produced it, so
a "wrong" agent that still solves the task gets credit. That is intentional: it lets
us separate *routing accuracy* (did we pick the labelled specialist?) from *end-task
success* (did the user get a correct answer?).

Security note: the Python checker executes model-generated code. It runs in a
separate interpreter in isolated mode (-I), with no inherited environment, a CPU-time
limit and a wall-clock timeout, inside a temp dir. That is enough for a benchmark run
inside the worker container, but it is NOT a security sandbox; see docs/limitations.md.
"""

from __future__ import annotations

import math
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from adaptiveroute.config import PROJECT_ROOT
from adaptiveroute.evaluation.dataset import (
    Check,
    ConstraintsCheck,
    ContainsCheck,
    NumericCheck,
    PythonCheck,
    SqlCheck,
)

RETAIL_SQL = PROJECT_ROOT / "data" / "eval" / "retail.sql"


@dataclass(frozen=True, slots=True)
class CheckResult:
    passed: bool
    detail: str


def check_output(check: Check, output: str | None) -> CheckResult:
    if output is None or not output.strip():
        return CheckResult(False, "empty output")
    match check:
        case NumericCheck():
            return _check_numeric(check, output)
        case PythonCheck():
            return _check_python(check, output)
        case SqlCheck():
            return _check_sql(check, output)
        case ConstraintsCheck():
            return _check_constraints(check, output)
        case ContainsCheck():
            return _check_contains(check, output)


# --- numeric ----------------------------------------------------------------------

_NUMBER = r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?|-?\.\d+"
_ANSWER_LINE = re.compile(
    rf"ANSWER\s*[:=]\s*\$?\s*(?P<num>{_NUMBER})(?:\s*/\s*(?P<den>{_NUMBER}))?", re.I
)
_ANY_NUMBER = re.compile(_NUMBER)
_BOXED = re.compile(rf"\\boxed\{{\s*\$?(?P<num>{_NUMBER})\s*\}}")


def _to_float(text: str) -> float:
    return float(text.replace(",", ""))


def extract_number(output: str) -> float | None:
    """Prefer an explicit 'ANSWER: x' line, then \\boxed{x}, else the last number."""
    matches = list(_ANSWER_LINE.finditer(output))
    if matches:
        m = matches[-1]
        value = _to_float(m["num"])
        if m["den"]:
            den = _to_float(m["den"])
            return value / den if den else None
        return value
    boxed = list(_BOXED.finditer(output))
    if boxed:
        return _to_float(boxed[-1]["num"])
    numbers = _ANY_NUMBER.findall(output)
    return _to_float(numbers[-1]) if numbers else None


def _check_numeric(check: NumericCheck, output: str) -> CheckResult:
    got = extract_number(output)
    if got is None:
        return CheckResult(False, "no number found")
    ok = math.isclose(got, check.answer, rel_tol=check.rel_tol, abs_tol=check.abs_tol)
    return CheckResult(ok, f"expected {check.answer}, got {got}")


# --- python -------------------------------------------------------------------------

_CODE_BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S | re.I)


def extract_code(output: str, entrypoint: str) -> str:
    """Pick the fenced block that defines the entrypoint (else the longest block)."""
    blocks = _CODE_BLOCK.findall(output)
    if not blocks:
        return output
    defining = [b for b in blocks if re.search(rf"\bdef {re.escape(entrypoint)}\b", b)]
    return str(max(defining or blocks, key=len))


# Prepended to the candidate program (instead of preexec_fn, which is unsafe when the
# checker is called from worker threads).
_CHILD_PRELUDE = """\
import resource as _r
_r.setrlimit(_r.RLIMIT_CPU, ({cpu}, {cpu}))
del _r
"""


def _check_python(check: PythonCheck, output: str) -> CheckResult:
    code = extract_code(output, check.entrypoint)
    if not re.search(
        rf"\bdef {re.escape(check.entrypoint)}\b|\b{re.escape(check.entrypoint)}\s*=", code
    ):
        return CheckResult(False, f"no definition of {check.entrypoint}")
    program = (
        _CHILD_PRELUDE.format(cpu=int(check.timeout_s) + 1)
        + code
        + "\n\n# --- tests ---\n"
        + "\n".join(check.tests)
        + "\nprint('ALL_PASSED')\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "candidate.py"
        script.write_text(program)
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, untrusted code isolated as documented
                [sys.executable, "-I", str(script)],
                cwd=tmp,
                env={},
                capture_output=True,
                text=True,
                timeout=check.timeout_s,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(False, f"timed out after {check.timeout_s}s")
    if proc.returncode == 0 and "ALL_PASSED" in proc.stdout:
        return CheckResult(True, "all tests passed")
    err = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
    return CheckResult(False, f"tests failed: {err[:200]}")


# --- sql ------------------------------------------------------------------------------

_SQL_BLOCK = re.compile(r"```sql[ \t]*\n(.*?)```", re.S | re.I)
_BARE_SELECT = re.compile(r"((?:WITH|SELECT)\b.*?)(?:;|\Z)", re.S | re.I)


def extract_sql(output: str) -> str | None:
    blocks = _SQL_BLOCK.findall(output)
    if blocks:
        return str(blocks[-1]).strip().rstrip(";")
    m = _BARE_SELECT.search(output)
    return m.group(1).strip() if m else None


@lru_cache(maxsize=1)
def _retail_script() -> str:
    return RETAIL_SQL.read_text()


def _run_query(sql: str, timeout_s: float = 5.0) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_retail_script())
        conn.execute("PRAGMA query_only = ON")  # candidate SQL cannot modify data
        deadline = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _normalise_rows(rows: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    def norm(v: object) -> object:
        if isinstance(v, float):
            return round(v, 2) + 0.0  # +0.0 turns -0.0 into 0.0
        return v

    return [tuple(norm(v) for v in row) for row in rows]


def _check_sql(check: SqlCheck, output: str) -> CheckResult:
    sql = extract_sql(output)
    if sql is None:
        return CheckResult(False, "no SQL found")
    try:
        got = _normalise_rows(_run_query(sql))
    except sqlite3.Error as exc:
        return CheckResult(False, f"SQL error: {exc}")
    expected = _normalise_rows(_run_query(check.reference_sql))
    if not check.order_matters:
        got, expected = sorted(got, key=repr), sorted(expected, key=repr)
    if got == expected:
        return CheckResult(True, f"{len(got)} rows match")
    return CheckResult(False, f"result mismatch: got {got[:3]}..., expected {expected[:3]}...")


# --- writing constraints -----------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+\S", re.M)
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9'\u2019-]+", text)  # \u2019 = curly apostrophe


def _check_constraints(check: ConstraintsCheck, output: str) -> CheckResult:
    text = output.strip()
    low = text.lower()
    n_words = len(_words(text))
    failures: list[str] = []
    if check.max_words is not None and n_words > check.max_words:
        failures.append(f"{n_words} words > max {check.max_words}")
    if check.min_words is not None and n_words < check.min_words:
        failures.append(f"{n_words} words < min {check.min_words}")
    for phrase in check.must_include:
        if phrase.lower() not in low:
            failures.append(f"missing '{phrase}'")
    for phrase in check.must_not_include:
        if re.search(rf"\b{re.escape(phrase.lower())}\b", low):
            failures.append(f"contains forbidden '{phrase}'")
    if check.bullet_count is not None:
        bullets = len(_BULLET.findall(text))
        if bullets != check.bullet_count:
            failures.append(f"{bullets} bullets != {check.bullet_count}")
    if check.max_sentences is not None:
        sentences = len(_SENTENCE_END.findall(text)) or 1
        if sentences > check.max_sentences:
            failures.append(f"{sentences} sentences > max {check.max_sentences}")
    if check.ends_with is not None and not text.rstrip().endswith(check.ends_with):
        failures.append(f"does not end with '{check.ends_with}'")
    return CheckResult(not failures, "; ".join(failures) or "all constraints met")


# --- factual answers ---------------------------------------------------------------------


def _fold(text: str) -> str:
    """Lower-case and strip accents so 'Sao Paulo' matches 'São Paulo'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(_fold(phrase))}(?!\w)", haystack) is not None


def _check_contains(check: ContainsCheck, output: str) -> CheckResult:
    text = _fold(output)
    hit = next((a for a in check.any_of if _contains_phrase(text, a)), None)
    if hit is None:
        return CheckResult(False, f"none of {check.any_of} found")
    bad = [p for p in check.none_of if _contains_phrase(text, p)]
    if bad:
        return CheckResult(False, f"contains wrong answer(s) {bad}")
    return CheckResult(True, f"found '{hit}'")


__all__ = ["CheckResult", "check_output", "extract_code", "extract_number", "extract_sql"]
