from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from app.nlp.normalize import find_quantities, normalize_text, number_word_value


def vals(text):
    return [q.value for q in find_quantities(text)[1]]


# ------------------------------------------------------------ normalisation
def test_devanagari_digits_become_ascii():
    assert normalize_text("वय ३५, उत्पन्न २,५०,०००") == "वय 35, उत्पन्न 2,50,000"


def test_nukta_and_chandrabindu_variants_match_the_same_entry():
    assert normalize_text("हज़ार") == normalize_text("हजार")
    assert normalize_text("करोड़") == normalize_text("करोड")
    assert normalize_text("पाँच") == normalize_text("पांच")


def test_zero_width_and_spaces_removed_and_latin_lowercased():
    assert normalize_text("  Rs.​  2  LAKH ") == "rs. 2 lakh"


@given(st.text(max_size=60))
def test_normalise_is_idempotent(t):
    once = normalize_text(t)
    assert normalize_text(once) == once


# ------------------------------------------------------------ amounts
@pytest.mark.parametrize("text,expected", [
    ("income is ₹2,50,000", 250000), ("annual income 250000", 250000), ("250,000", 250000),
    ("2.5 lakh", 250000), ("2 lakhs", 200000), ("1.5 lac", 150000), ("3 crore", 30000000),
    ("50k", 50000), ("80 thousand", 80000), ("Rs. 2 lakh", 200000), ("INR 8 lakh", 800000),
    ("२ लाख", 200000), ("२.५ लाख", 250000), ("८० हज़ार", 80000), ("१ करोड़", 10**7),
    ("पाँच लाख", 500000), ("दोन लाख", 200000), ("आठ लाख रुपये", 800000), ("बीस हज़ार", 20000),
    ("डेढ़ लाख", 150000), ("ढाई लाख", 250000), ("सवा लाख", 125000), ("अडीच लाख", 250000),
    ("दीड लाख", 150000), ("सव्वा लाख", 125000), ("दो सौ", 200), ("सहा हजार", 6000),
])
def test_amounts(text, expected):
    assert vals(text) == [Decimal(expected)]


def test_rupee_marker_is_reported():
    q = find_quantities("₹2,50,000")[1][0]
    assert q.currency_marked and q.as_int == 250000
    assert not find_quantities("2 lakh")[1][0].currency_marked


def test_as_int_is_none_for_fractions_of_a_rupee():
    assert find_quantities("0.0001 lakh")[1][0].as_int == 10
    assert find_quantities("2.5")[1][0].as_int is None


@pytest.mark.parametrize("text", ["2-3 lakh", "2 से 3 लाख", "2 to 3 lakh", "१ ते २ लाख", "1,2 lakh", "डेढ़", "सवा"])
def test_ambiguous_or_incomplete_gives_nothing_rather_than_a_guess(text):
    assert vals(text) == []


def test_bare_number_words_in_prose_are_not_amounts():
    assert vals("one of my friends has ten books") == []
    assert vals("ten lakh") == [Decimal(10**6)]
    assert number_word_value("पचास") == 50 and number_word_value("twenty") == 20 and number_word_value("zzz") is None


def test_age_and_income_in_one_sentence_are_both_found_in_order():
    assert vals("I am 30 years old and my family income is 2.5 lakh") == [Decimal(30), Decimal(250000)]
    assert vals("मी ३० वर्षांची आहे, उत्पन्न दोन लाख") == [Decimal(30), Decimal(200000)]


def test_spans_point_into_the_normalised_text():
    norm, qs = find_quantities("मेरी आय २ लाख है")
    assert norm[qs[0].start:qs[0].end] == qs[0].raw == "2 लाख"


def test_word_boundaries_do_not_split_words():
    assert vals("lakhimpur 5") == [Decimal(5)]          # 'lakh' inside a place name is not a multiplier
    assert vals("kilo 5kg") == [Decimal(5)]             # '5kg' is 5, never 5,000


@given(st.integers(min_value=1, max_value=10**9))
def test_indian_grouping_roundtrip(n):
    s = str(n)
    if len(s) > 3:
        head, tail, groups = s[:-3], s[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    assert vals(f"₹{s}") == [Decimal(n)]
