import itertools
from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given, settings, strategies as st

from app.domain.eligibility import (
    Group, Rule, SchemeRules, Status, Tri, combine, evaluate_group, evaluate_scheme, rules_hash,
)

TODAY = date(2026, 10, 31)
P, F, U, O = Tri.PASS, Tri.FAIL, Tri.UNKNOWN, Tri.OFFICIAL


def r(field, op, value=None, **kw):
    return Rule(field, op, value, source_url="https://pib.gov.in/x", locator="p1", **kw)


def scheme(eligibility=None, exclusions=None, **kw):
    base = dict(scheme_id="s", eligibility=eligibility, exclusions=exclusions, rules_complete=True, status="active",
                verification_status="source_verified", last_verified=date(2026, 10, 1),
                next_review_due=date(2027, 4, 1))
    base.update(kw)
    return SchemeRules(**base)


def leaf(rule, profile, today=TODAY):
    s = scheme(Group("AND", (rule,)))
    return evaluate_scheme(s, profile, today=today).results[0]


# ----------------------------------------------------------------- operators and boundaries
@pytest.mark.parametrize("op,value,x,expected", [
    ("gte", 18, 17, F), ("gte", 18, 18, P), ("gte", 18, 19, P),
    ("lte", 250000, 250000, P), ("lte", 250000, 250001, F),
    ("gt", 18, 18, F), ("gt", 18, 19, P),
    ("lt", 18, 18, F), ("lt", 18, 17, P),
    ("between", [18, 30], 17, F), ("between", [18, 30], 18, P), ("between", [18, 30], 30, P), ("between", [18, 30], 31, F),
    ("eq", 18, 18, P), ("neq", 18, 18, F), ("neq", 18, 19, P),
    ("in", [18, 19], 19, P), ("in", [18, 19], 20, F), ("not_in", [18, 19], 19, F), ("not_in", [18, 19], 20, P),
])
def test_numeric_operators_with_inclusive_boundaries(op, value, x, expected):
    assert leaf(r("age_years", op, value), {"age_years": x}).result is expected


def test_money_boundary_is_whole_rupees():
    rule = r("annual_family_income", "lte", 150000)
    assert leaf(rule, {"annual_family_income": 150000}).result is P
    assert leaf(rule, {"annual_family_income": 150001}).result is F


def test_enum_and_string_matching_ignores_case_and_spaces():
    assert leaf(r("state_code", "eq", "MH"), {"state_code": " mh "}).result is P
    assert leaf(r("state_code", "in", ["MH", "GJ"]), {"state_code": "gj"}).result is P
    assert leaf(r("state_code", "eq", "MH"), {"state_code": "GJ"}).result is F


def test_bool_operators():
    assert leaf(r("owns_agricultural_land", "is_true"), {"owns_agricultural_land": True}).result is P
    assert leaf(r("owns_agricultural_land", "is_true"), {"owns_agricultural_land": False}).result is F
    assert leaf(r("owns_agricultural_land", "is_false"), {"owns_agricultural_land": False}).result is P


# ----------------------------------------------------------------- unknown is never a pass
@pytest.mark.parametrize("profile", [{}, {"age_years": None}, {"age_years": ""}, {"age_years": "  "}])
def test_missing_value_is_unknown(profile):
    res = leaf(r("age_years", "gte", 18), profile)
    assert res.result is U and res.reason_code == "missing_value"


@pytest.mark.parametrize("bad", ["18", True, -1, 17.5, [18], {"a": 1}])
def test_invalid_age_values_are_unknown_not_guessed(bad):
    res = leaf(r("age_years", "gte", 18), {"age_years": bad})
    assert res.result is U and res.reason_code == "invalid_value"


def test_whole_number_float_is_accepted():
    assert leaf(r("age_years", "gte", 18), {"age_years": 18.0}).result is P


@pytest.mark.parametrize("field,bad", [("owns_agricultural_land", "yes"), ("owns_agricultural_land", 1),
                                       ("state_code", "ZZ"), ("state_code", 5), ("residence_area", "suburb")])
def test_invalid_bool_and_enum_values_are_unknown(field, bad):
    op = "is_true" if field == "owns_agricultural_land" else "eq"
    value = None if op == "is_true" else ("MH" if field == "state_code" else "rural")
    assert leaf(r(field, op, value), {field: bad}).result is U


def test_unverified_and_official_only_rules_never_pass():
    assert leaf(r("age_years", "gte", 18, verified=False), {"age_years": 99}).result is O
    assert leaf(r("age_years", "gte", 18, evaluability="official_only"), {"age_years": 99}).result is O


# ----------------------------------------------------------------- age with as_of (birthday unknown)
@pytest.mark.parametrize("age,expected", [(17, U), (18, P), (16, F)])
def test_future_as_of_age_uses_possible_birthdays(age, expected):
    rule = r("age_years", "gte", 18, as_of=date(2027, 3, 31))
    assert leaf(rule, {"age_years": age}).result is expected


def test_past_as_of_age():
    rule = r("age_years", "lte", 19, as_of=date(2026, 7, 1))
    res = leaf(rule, {"age_years": 20})  # could have been 19 or 20 on 1 July
    assert res.result is U and res.reason_code == "age_depends_on_birthday"
    assert leaf(rule, {"age_years": 19}).result is P  # 18 or 19 then
    assert leaf(rule, {"age_years": 21}).result is F  # 20 or 21 then


def test_as_of_on_an_exact_anniversary_has_one_candidate():
    rule = r("age_years", "gte", 19, as_of=date(2027, 10, 31))
    assert leaf(rule, {"age_years": 18}).result is P
    assert leaf(rule, {"age_years": 17}).result is F


def test_as_of_equal_to_today_is_todays_age():
    assert leaf(r("age_years", "gte", 18, as_of=TODAY), {"age_years": 18}).result is P


def test_unknown_age_as_of_reports_birth_date_needed():
    s = scheme(Group("AND", (r("age_years", "gte", 18, as_of=date(2027, 3, 31)),)))
    a = evaluate_scheme(s, {"age_years": 17}, today=TODAY)
    assert [(m.field, m.reason) for m in a.missing_fields] == [("age_years", "birth_date_needed")]


def test_as_of_on_other_fields_is_only_a_note():
    res = leaf(r("annual_family_income", "lte", 100, as_of=date(2027, 1, 1)), {"annual_family_income": 50})
    assert res.result is P and res.params["as_of"] == "2027-01-01"


# ----------------------------------------------------------------- Kleene combination tables
@pytest.mark.parametrize("logic,inputs,expected", [
    ("AND", [P, P], P), ("AND", [P, F], F), ("AND", [P, U], U), ("AND", [F, U], F), ("AND", [P, O], O),
    ("AND", [U, O], U), ("AND", [F, O], F),
    ("OR", [F, F], F), ("OR", [F, P], P), ("OR", [F, U], U), ("OR", [P, U], P), ("OR", [F, O], O),
    ("OR", [U, O], U), ("OR", [P, O], P),
    ("NOT_ANY", [F, F], P), ("NOT_ANY", [F, P], F), ("NOT_ANY", [F, U], U), ("NOT_ANY", [P, U], F),
    ("NOT_ANY", [F, O], O), ("NOT_ANY", [U, O], U),
])
def test_combination_tables(logic, inputs, expected):
    assert combine(logic, inputs) is expected
    assert combine(logic, list(reversed(inputs))) is expected  # order does not matter


def test_empty_group_and_bad_logic_are_errors():
    with pytest.raises(ValueError):
        combine("AND", [])
    with pytest.raises(ValueError):
        combine("XOR", [P])


# ----------------------------------------------------------------- scheme level
RULES = Group("AND", (r("age_years", "between", [18, 30]), r("annual_family_income", "lte", 250000)))
OK = {"age_years": 21, "annual_family_income": 150000}


def status(s, profile, today=TODAY):
    return evaluate_scheme(s, profile, today=today)


def test_likely_eligible():
    a = status(scheme(RULES), OK)
    assert a.status is Status.LIKELY_ELIGIBLE and a.top is P and not a.missing_fields and not a.reasons


def test_likely_ineligible_needs_a_trusted_record():
    bad = {"age_years": 40, "annual_family_income": 150000}
    assert status(scheme(RULES), bad).status is Status.LIKELY_INELIGIBLE
    for kw, reason in [({"rules_complete": False}, "rules_incomplete"),
                       ({"next_review_due": date(2026, 10, 1)}, "record_stale")]:
        a = status(scheme(RULES, **kw), bad)
        assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and reason in a.reasons


def test_missing_income_is_insufficient_information_and_asks_for_it():
    a = status(scheme(RULES), {"age_years": 21})
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert [m.field for m in a.missing_fields] == ["annual_family_income"]


def test_a_definite_fail_beats_missing_data():
    a = status(scheme(RULES), {"age_years": 40})
    assert a.status is Status.LIKELY_INELIGIBLE and a.missing_fields == ()  # income cannot change the outcome


def test_empty_profile_is_insufficient_and_lists_decisive_fields():
    a = status(scheme(RULES), {})
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert {m.field for m in a.missing_fields} == {"age_years", "annual_family_income"}


@pytest.mark.parametrize("verification", ["draft"])
def test_draft_never_gives_a_verdict(verification):
    for profile in (OK, {"age_years": 40}, {}):
        a = status(scheme(RULES, verification_status=verification), profile)
        assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and a.reasons == ("record_is_draft",)


def test_no_rules_requires_verification():
    a = status(scheme(None), OK)
    assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and a.reasons == ("no_rules",)


@pytest.mark.parametrize("kw,reason", [
    ({"rules_complete": False}, "rules_incomplete"),
    ({"next_review_due": date(2026, 10, 30)}, "record_stale"),
    ({"status": "closed"}, "scheme_status_closed"),
    ({"status": "unknown"}, "scheme_status_unknown"),
])
def test_eligible_pass_is_downgraded_when_record_is_doubtful(kw, reason):
    a = status(scheme(RULES, **kw), OK)
    assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and reason in a.reasons


def test_official_only_rule_blocks_a_clean_verdict():
    g = Group("AND", RULES.items + (r("occupation", "eq", "farmer", evaluability="official_only"),))
    a = status(scheme(g), OK)
    assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and "official_only_rules" in a.reasons
    # but a certain failure elsewhere still shows
    assert status(scheme(g), {"age_years": 40}).top is F


def test_deadline_passed_is_only_a_warning():
    a = status(scheme(RULES, application_deadline=date(2026, 10, 1)), OK)
    assert a.status is Status.LIKELY_ELIGIBLE and a.warnings == ("deadline_passed",)


def test_stale_boundary_is_exclusive():
    assert status(scheme(RULES, next_review_due=TODAY), OK).status is Status.LIKELY_ELIGIBLE
    assert "record_stale" in status(scheme(RULES, next_review_due=date(2026, 10, 30)), OK).warnings


def test_exclusions_apply_pass_means_excluded():
    ex = Group("NOT_ANY", (r("receives_other_scholarship", "is_true"),))
    s = scheme(RULES, ex)
    assert status(s, {**OK, "receives_other_scholarship": False}).status is Status.LIKELY_ELIGIBLE
    a = status(s, {**OK, "receives_other_scholarship": True})
    assert a.status is Status.LIKELY_INELIGIBLE
    assert [x.purpose for x in a.results if x.rule.field == "receives_other_scholarship"] == ["exclusion"]
    a = status(s, OK)  # not answered
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert [m.field for m in a.missing_fields] == ["receives_other_scholarship"]


def test_or_group_with_unknown_branch_can_still_pass():
    g = Group("OR", (r("occupation", "eq", "student"), r("employment_status", "eq", "unemployed")))
    s = scheme(g)
    assert status(s, {"occupation": "student"}).top is P
    a = status(s, {"occupation": "farmer"})
    assert a.top is U and [m.field for m in a.missing_fields] == ["employment_status"]


def test_sensitive_flag_on_missing_fields():
    s = scheme(Group("AND", (r("gender", "eq", "female"), r("age_years", "gte", 18))))
    flags = {m.field: m.sensitive for m in status(s, {}).missing_fields}
    assert flags == {"gender": True, "age_years": False}


def test_conditional_group_evaluation():
    g = Group("AND", (r("gender", "eq", "female"),))
    assert evaluate_group(g, {"gender": "female"}, today=TODAY)[0] is P
    assert evaluate_group(g, {"gender": "male"}, today=TODAY)[0] is F
    assert evaluate_group(g, {}, today=TODAY)[0] is U


# ----------------------------------------------------------------- determinism, purity, hash
def test_evaluation_is_deterministic_and_does_not_mutate_the_profile():
    profile = {"age_years": 21, "annual_family_income": 150000}
    snapshot = dict(profile)
    a, b = status(scheme(RULES), profile), status(scheme(RULES), profile)
    assert a == b and profile == snapshot


def test_rules_hash_ignores_order_and_changes_with_a_rule():
    flipped = Group("AND", tuple(reversed(RULES.items)))
    assert rules_hash(scheme(RULES)) == rules_hash(scheme(flipped))
    changed = Group("AND", (r("age_years", "between", [18, 31]), RULES.items[1]))
    assert rules_hash(scheme(RULES)) != rules_hash(scheme(changed))
    assert rules_hash(scheme(RULES)) != rules_hash(scheme(replace(RULES, items=RULES.items[:1])))


# ----------------------------------------------------------------- soundness (property test)
AGES, INCOMES = [5, 18, 30, 60], [0, 100000, 250000, 900000]
DOMAIN = {"age_years": AGES, "annual_family_income": INCOMES,
          "owns_agricultural_land": [True, False], "has_bank_account": [True, False]}
LEAVES = [r("age_years", "between", [18, 30]), r("age_years", "gte", 60), r("annual_family_income", "lte", 250000),
          r("annual_family_income", "gt", 100000), r("owns_agricultural_land", "is_true"),
          r("has_bank_account", "is_false"), r("age_years", "in", [5, 18])]


def trees(depth):
    leaf_s = st.sampled_from(LEAVES)
    if depth == 0:
        return leaf_s
    sub = st.lists(st.deferred(lambda: trees(depth - 1)), min_size=1, max_size=3)
    return st.one_of(leaf_s, st.builds(lambda lg, xs: Group(lg, tuple(xs)), st.sampled_from(["AND", "OR"]), sub))


def as_group(t):
    return t if isinstance(t, Group) else Group("AND", (t,))


@settings(max_examples=300, deadline=None)
@given(tree=trees(2), excl=st.lists(st.sampled_from(LEAVES), max_size=2),
       known=st.fixed_dictionaries({}, optional={k: st.sampled_from(v) for k, v in DOMAIN.items()}))
def test_certain_results_hold_for_every_completion_of_the_unknowns(tree, excl, known):
    s = scheme(as_group(tree), Group("NOT_ANY", tuple(excl)) if excl else None)
    top = evaluate_scheme(s, known, today=TODAY).top
    assert top in (P, F, U)
    if top is U:
        return
    unknown = [k for k in DOMAIN if k not in known]
    for combo in itertools.product(*(DOMAIN[k] for k in unknown)):
        full = {**known, **dict(zip(unknown, combo))}
        assert evaluate_scheme(s, full, today=TODAY).top is top
