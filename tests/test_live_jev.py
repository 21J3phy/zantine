"""Live smoke test: one real Jev batch (a Choice and a Noul), checking the API shape.

Costs real money (a fraction of a cent). Runs only with
``ZANTINE_LIVE=1 uv run pytest --live`` and ``TYPESAFE_API_KEY`` set; capped at $0.05.
"""

from __future__ import annotations

import pytest

from zantine.harness.ledger import Ledger
from zantine.judge import Batch, Question, parse_answers
from zantine.judge.jev import JevJudge


@pytest.mark.live
def test_jev_answers_a_typed_batch() -> None:
    ledger = Ledger(0.05)
    judge = JevJudge.from_env(ledger, live=True)
    batch = Batch(
        "smoke",
        {"statements": [{"id": "s1", "speaker": "Red", "text": "I saw Blue vent. Vote Blue."}]},
        {
            "push_s1": Question(
                "choice",
                "Whom does statement s1 argue the group should vote to eject?",
                {"Blue": "Eject Blue.", "Green": "Eject Green.", "skip": "Skip the vote."},
            ),
            "asserted_s1": Question("noul", "Does statement s1 state something as a fact?"),
        },
    )
    answers = parse_answers(batch, judge.judge(batch), 0.5)
    assert answers["push_s1"].value == "Blue"
    assert answers["push_s1"].probabilities
    assert 0 <= float(answers["asserted_s1"].value) <= 1
    (record,) = ledger.records
    assert record.purpose == "jev" and record.ok and record.input_tokens > 0
    assert not judge.info()["usage_estimated"]
