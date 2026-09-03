"""The deterministic evaluators and the text primitives beneath them."""

from __future__ import annotations

import pytest
from app.evaluation import (
    evaluate_consistency,
    evaluate_correctness,
    evaluate_faithfulness,
    evaluate_format,
    evaluate_relevance,
)
from app.evaluation.text import (
    content_words,
    currency_amounts,
    dates,
    flatten_text,
    numbers,
    overlap,
)

CONTEXT = "Customers may return any item within 30 days of delivery for a full refund."


# -- text primitives -----------------------------------------------------


def test_content_words_drop_stopwords_and_single_characters():
    assert content_words("The cat is on a mat") == {"cat", "mat"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30 days", {"30"}),
        ("1,200 items", {"1200"}),
        ("a 3.5 rating", {"3.5"}),
        ("no digits here", set()),
        ("version 2 costs 1,000.50", {"2", "1000.5"}),
    ],
)
def test_numbers_are_extracted_and_normalised(text, expected):
    assert numbers(text) == expected


def test_thousands_separators_do_not_change_a_value():
    assert numbers("1,200") == numbers("1200")


def test_dates_are_extracted_in_several_formats():
    found = dates("Effective 2026-01-01, revised 3/15/2026, replaced Jan 5, 2027")
    assert len(found) == 3


def test_currency_amounts_are_extracted():
    assert currency_amounts("costs $1,200.00 or 900 EUR") == {"$1,200.00", "900EUR"}


def test_overlap_is_asymmetric():
    # "Is the answer contained in the context?" is a different question from
    # "does the context cover the answer?", and the score reflects that.
    assert overlap("refund", "refund policy details") == 1.0
    assert overlap("refund policy details", "refund") < 1.0


def test_overlap_of_empty_text_is_zero():
    assert overlap("", "anything") == 0.0
    assert overlap("the a of", "anything") == 0.0


def test_flatten_text_walks_nested_payloads():
    flat = flatten_text({"docs": [{"text": "alpha"}, {"text": "beta"}], "n": 3})
    assert "alpha" in flat and "beta" in flat and "3" in flat


def test_flatten_text_terminates_on_deep_structures():
    payload: dict = {}
    cursor = payload
    for _ in range(40):
        cursor["next"] = {}
        cursor = cursor["next"]
    assert isinstance(flatten_text(payload), str)


# -- correctness ---------------------------------------------------------


def test_exact_match_scores_one():
    result = evaluate_correctness("30 days", "30 days")
    assert result.score == 1.0
    assert result.passed


def test_case_differences_are_still_an_exact_match():
    assert evaluate_correctness("Thirty Days", "thirty days").score == 1.0


def test_a_wrong_number_caps_the_score_however_close_the_wording():
    result = evaluate_correctness(
        "Customers have 45 days to return an item.",
        "Customers have 30 days to return an item.",
    )
    assert not result.passed
    assert result.score <= 0.5
    assert result.detail["missing_numbers"] == ["30"]
    assert result.detail["unexpected_numbers"] == ["45"]


def test_paraphrase_without_numeric_error_scores_on_wording():
    result = evaluate_correctness(
        "You may send it back within 30 days.", "Returns are accepted within 30 days."
    )
    assert 0.0 < result.score < 1.0
    assert result.detail["missing_numbers"] == []


def test_a_missing_expected_answer_is_reported_not_assumed_correct():
    result = evaluate_correctness("anything", "")
    assert result.score == 0.0
    assert "no expected answer" in result.explanation


# -- relevance -----------------------------------------------------------


def test_relevance_of_an_empty_result_set_is_zero():
    result = evaluate_relevance("refund policy", [])
    assert result.score == 0.0
    assert result.detail["document_count"] == 0


def test_relevance_uses_the_best_document_not_the_average():
    result = evaluate_relevance(
        "refund policy days",
        [{"text": "Shipping information"}, {"text": "The refund policy allows 30 days"}],
    )
    assert result.score == result.detail["best_document_score"]
    assert result.score > min(result.detail["per_document_scores"])


def test_an_off_topic_pool_fails_the_threshold():
    result = evaluate_relevance("refund policy", [{"text": "Shipping takes five days"}])
    assert not result.passed


def test_documents_can_be_supplied_wrapped_in_a_dict():
    result = evaluate_relevance("refund policy", {"documents": [{"text": "refund policy"}]})
    assert result.detail["document_count"] == 1


# -- faithfulness --------------------------------------------------------


def test_a_grounded_answer_is_faithful():
    result = evaluate_faithfulness("Customers have 30 days to return an item.", CONTEXT)
    assert result.passed


def test_one_ungrounded_number_caps_the_faithfulness_score():
    result = evaluate_faithfulness("Customers have 45 days to return an item.", CONTEXT)
    assert not result.passed
    assert result.score <= 0.4
    assert result.detail["ungrounded_numbers"] == ["45"]


def test_an_ungrounded_date_is_caught():
    result = evaluate_faithfulness("The policy changed on 2027-01-01.", CONTEXT)
    assert result.detail["ungrounded_dates"] == ["2027-01-01"]


def test_an_empty_answer_is_not_faithful():
    assert evaluate_faithfulness("", CONTEXT).score == 0.0


def test_an_answer_with_no_context_cannot_be_grounded():
    result = evaluate_faithfulness("Customers have 30 days.", "")
    assert result.score == 0.0
    assert "no context" in result.explanation


# -- format --------------------------------------------------------------


def test_format_passes_when_every_required_field_is_present():
    result = evaluate_format({"documents": [1], "query": "q"}, ["documents", "query"])
    assert result.passed
    assert result.score == 1.0


def test_missing_and_empty_fields_are_reported_separately():
    result = evaluate_format({"documents": [], "query": "q"}, ["documents", "query", "top_k"])
    assert result.detail["missing_fields"] == ["top_k"]
    assert result.detail["empty_fields"] == ["documents"]


def test_type_errors_are_reported():
    result = evaluate_format({"documents": "one"}, ["documents"], {"documents": list})
    assert result.detail["type_errors"]
    assert not result.passed


def test_a_non_object_payload_fails_when_fields_are_required():
    assert evaluate_format("just a string", ["documents"]).score == 0.0


def test_a_non_object_payload_passes_when_nothing_is_required():
    assert evaluate_format("just a string").passed


# -- consistency ---------------------------------------------------------


def test_a_value_seen_identically_everywhere_is_consistent():
    result = evaluate_consistency({"preprocess": "u-42", "retrieval": "u-42", "llm": "u-42"})
    assert result.passed
    assert result.score == 1.0


def test_a_value_that_changed_is_inconsistent():
    result = evaluate_consistency({"retrieval": "doc-1", "prompt": "doc-2"})
    assert not result.passed
    assert result.detail["distinct_values"] == ["doc-1", "doc-2"]


def test_a_single_observation_cannot_be_inconsistent():
    result = evaluate_consistency({"retrieval": "doc-1"})
    assert result.passed
    assert "nothing to compare" in result.explanation


def test_equivalent_values_of_different_container_types_agree():
    result = evaluate_consistency({"a": ["x", "y"], "b": ("x", "y")})
    assert result.passed


def test_whitespace_and_case_do_not_make_strings_differ():
    assert evaluate_consistency({"a": " USD ", "b": "usd"}).passed


def test_the_score_falls_as_more_distinct_values_appear():
    two = evaluate_consistency({"a": 1, "b": 2}).score
    three = evaluate_consistency({"a": 1, "b": 2, "c": 3}).score
    assert three < two
