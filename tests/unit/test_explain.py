from datetime import date

import pytest

from app.domain.eligibility import Group, Rule, SchemeRules, evaluate_scheme
from app.domain.explain import describe_requirement, explain_assessment, explain_result, format_inr

TODAY = date(2026, 10, 31)


def rule(field, op, value=None, **kw):
    return Rule(field, op, value, source_url="https://pib.gov.in/x", locator="p1", **kw)


def run(eligibility, profile, exclusions=None):
    s = SchemeRules("s", eligibility, exclusions, True, "active", "source_verified",
                    next_review_due=date(2027, 4, 1))
    return evaluate_scheme(s, profile, today=TODAY)


@pytest.mark.parametrize("n,text", [(0, "₹0"), (999, "₹999"), (1000, "₹1,000"), (150000, "₹1,50,000"),
                                    (1234567, "₹12,34,567"), (10000000, "₹1,00,00,000")])
def test_indian_digit_grouping(n, text):
    assert format_inr(n) == text


def test_requirement_wording():
    assert describe_requirement(rule("annual_family_income", "lte", 150000)) == "at most ₹1,50,000"
    assert describe_requirement(rule("age_years", "between", [18, 30])) == "between 18 and 30 (both included)"
    assert describe_requirement(rule("residence_area", "eq", "rural")) == "must be rural"


def test_pass_fail_missing_lines():
    a = run(Group("AND", (rule("age_years", "gte", 18), rule("annual_family_income", "lte", 150000),
                          rule("gender", "eq", "female"))), {"age_years": 17, "annual_family_income": 150000})
    lines = [explain_result(x) for x in a.results]
    assert "not met" in lines[0] and "17" in lines[0]
    assert "is met" in lines[1] and "₹1,50,000" in lines[1]
    assert "not provided" in lines[2] and "have not assumed" in lines[2]


def test_exclusion_wording_both_ways():
    ex = Group("NOT_ANY", (rule("receives_other_scholarship", "is_true"),))
    hit = run(Group("AND", (rule("age_years", "gte", 18),)), {"age_years": 20, "receives_other_scholarship": True}, ex)
    assert any("exclusion applies to you" in explain_result(x) for x in hit.results)
    miss = run(Group("AND", (rule("age_years", "gte", 18),)), {"age_years": 20, "receives_other_scholarship": False}, ex)
    assert any("does not apply" in explain_result(x) for x in miss.results)


def test_age_birthday_line():
    a = run(Group("AND", (rule("age_years", "gte", 18, as_of=date(2027, 3, 31)),)), {"age_years": 17})
    text = explain_result(a.results[0])
    assert "17 or 18" in text and "date of birth" in text and "2027-03-31" in text


def test_assessment_always_carries_the_preliminary_notice():
    a = run(Group("AND", (rule("age_years", "gte", 18),)), {"age_years": 20})
    out = explain_assessment(a)
    assert out["headline"] == "Likely eligible"
    assert "not an official decision" in out["notice"]


def test_unsupported_language_raises_instead_of_falling_back():
    a = run(Group("AND", (rule("age_years", "gte", 18),)), {"age_years": 20})
    with pytest.raises(ValueError):
        explain_assessment(a, "hi")
    with pytest.raises(ValueError):
        explain_result(a.results[0], "mr")
