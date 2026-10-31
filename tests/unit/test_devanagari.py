import sqlite3

import pytest

from app.utils.devanagari import fts5_tokenizer_spec, normalize_text

DOCS = [
    "शेतकरी कुटुंबांना वार्षिक ६००० रुपये मिळतात",   # Marathi
    "किसान परिवारों को सालाना 6000 रुपये मिलते हैं",     # Hindi
    "Farmer families receive Rs 6000 per year",          # English
]


def fts5_available() -> bool:
    try:
        sqlite3.connect(":memory:").execute("create virtual table x using fts5(a)")
        return True
    except sqlite3.Error:
        return False


pytestmark = pytest.mark.skipif(not fts5_available(), reason="SQLite build lacks FTS5")


def build(tokenize: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(f"create virtual table t using fts5(body, tokenize=\"{tokenize}\")")
    for d in DOCS:
        con.execute("insert into t values (?)", (normalize_text(d),))
    con.execute("create virtual table v using fts5vocab(t, 'row')")
    return con


def vocab(con):
    return {r[0] for r in con.execute("select term from v")}


def hits(con, query):
    return [r[0] for r in con.execute("select rowid from t where t match ? order by rowid",
                                      ('"%s"' % normalize_text(query),))]


def test_default_tokenizer_shreds_devanagari_words():
    """Documents WHY the custom tokenizer exists."""
    v = vocab(build("unicode61"))
    assert "शेतकरी" not in v
    assert "तकर" in v


def test_custom_tokenizer_keeps_whole_words():
    v = vocab(build(fts5_tokenizer_spec()))
    assert {"शेतकरी", "किसान", "रुपये", "वार्षिक"} <= v
    assert "तकर" not in v


def test_queries_match_only_the_right_language():
    con = build(fts5_tokenizer_spec())
    assert hits(con, "शेतकरी") == [1]
    assert hits(con, "किसान") == [2]
    assert hits(con, "Farmer") == [3]
    assert hits(con, "रुपये") == [1, 2]


def test_devanagari_and_ascii_digits_match_each_other():
    con = build(fts5_tokenizer_spec())
    assert hits(con, "६०००") == [1, 2, 3]
    assert hits(con, "6000") == [1, 2, 3]


def test_normalize_text_converts_digits_and_nfc():
    assert normalize_text("₹ १,५०,०००") == "₹ 1,50,000"
    assert normalize_text("é") == "é"  # decomposed accent composed to NFC
