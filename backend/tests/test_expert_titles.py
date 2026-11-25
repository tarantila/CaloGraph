from __future__ import annotations

import pytest

from app.expert.chat_contracts import ExpertAnswer
from app.expert.titles import generated_conversation_title


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Fiber\tintake\ntrend  ", "Fiber intake trend"),
        ("Äpfel und Gemüse", "Äpfel und Gemüse"),
        (None, None),
        (37, None),
        ("  \n\t", None),
        ("x" * 81, None),
        ("\ud800 title", None),
        ("\x00\x01", None),
    ],
)
def test_generated_title_normalizer_discards_malformed_values(value: object, expected: str | None) -> None:
    assert generated_conversation_title(value) == expected
    answer = ExpertAnswer.model_validate({"text": "Valid answer", "conversation_title": value})
    assert answer.text == "Valid answer"
    assert answer.conversation_title == expected


def test_conversation_title_is_not_in_contract_repr() -> None:
    answer = ExpertAnswer(text="Valid answer", conversation_title="Fiber intake")
    assert "Fiber intake" not in repr(answer)
