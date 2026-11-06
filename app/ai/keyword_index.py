"""Keyword search over chunks with SQLite FTS5.

SQLite's default tokenizer cuts Hindi and Marathi words at vowel signs, so the Devanagari combining marks are declared
part of a word. Text is normalised the same way for indexing and for queries (digits, nukta, chandrabindu, case).
This index holds only text; it needs no model and keeps working if the embedding libraries are not installed.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

from app.ai.chunks import Chunk
from app.nlp.normalize import normalize_text

# U+0900-0903, 093A-093C, 093E-094F, 0951-0957, 0962-0963: the marks that belong inside a Devanagari word
_MARKS = "".join(chr(c) for r in ((0x900, 0x903), (0x93A, 0x93C), (0x93E, 0x94F), (0x951, 0x957), (0x962, 0x963))
                 for c in range(r[0], r[1] + 1))
_TOKEN = re.compile(rf"[\w{_MARKS}]+", re.UNICODE)

# Function words only. They carry no topic, and with a small corpus they would otherwise dominate the ranking.
STOPWORDS = {
    "a", "an", "the", "is", "are", "am", "was", "do", "does", "i", "me", "my", "we", "you", "your", "to", "of", "for",
    "in", "on", "at", "and", "or", "it", "this", "that", "what", "which", "how", "can", "will", "be", "with", "from",
    "need", "get", "about", "tell", "please",
    "है", "हैं", "का", "की", "के", "को", "में", "से", "और", "या", "यह", "वह", "मैं", "मेरा", "मेरी", "क्या", "कैसे", "कौन", "कौन-से",
    "के लिए", "लिए", "पर", "भी", "तो", "हो", "सकता", "सकती", "चाहिए",
    "आहे", "आहेत", "चा", "ची", "चे", "ला", "ना", "मध्ये", "आणि", "किंवा", "हे", "ते", "मी", "माझा", "माझी", "काय", "कसे", "कोणते",
    "साठी", "वर", "पण", "होय", "शकतो", "शकते", "हवे", "पाहिजे",
}


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(normalize_text(text)) if t not in STOPWORDS and len(t) > 1 or t.isdigit()]


class KeywordIndex:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS kw_chunks (chunk_id TEXT PRIMARY KEY, scheme_id TEXT NOT NULL, "
                        "language TEXT NOT NULL, section TEXT NOT NULL, state_code TEXT)")
        self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS kw_fts USING fts5(chunk_id UNINDEXED, body, "
                        f"tokenize=\"unicode61 remove_diacritics 0 tokenchars '{_MARKS}'\")")

    def rebuild(self, chunks: Iterable[Chunk]) -> int:
        """Replace the whole index. A rebuild is cheap at this size and cannot leave stale rows behind."""
        with self.db:
            self.db.execute("DELETE FROM kw_chunks")
            self.db.execute("DELETE FROM kw_fts")
            n = 0
            for c in chunks:
                self.db.execute("INSERT OR REPLACE INTO kw_chunks VALUES (?,?,?,?,?)",
                                (c.chunk_id, c.scheme_id, c.language, c.section, c.state_code))
                self.db.execute("INSERT INTO kw_fts (chunk_id, body) VALUES (?,?)", (c.chunk_id, normalize_text(c.text)))
                n += 1
        return n

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM kw_chunks").fetchone()[0]

    def search(self, query: str, *, k: int = 10, languages: tuple[str, ...] | None = None,
               scheme_ids: tuple[str, ...] | None = None) -> list[tuple[str, float]]:
        """(chunk_id, score) best first; higher is better. Empty when the query has no usable words."""
        words = list(dict.fromkeys(tokens(query)))[:30]
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        sql = ("SELECT f.chunk_id, bm25(kw_fts) AS s FROM kw_fts f JOIN kw_chunks c ON c.chunk_id = f.chunk_id "
               "WHERE kw_fts MATCH ?")
        args: list = [match]
        if languages:
            sql += f" AND c.language IN ({','.join('?' * len(languages))})"
            args += list(languages)
        if scheme_ids:
            sql += f" AND c.scheme_id IN ({','.join('?' * len(scheme_ids))})"
            args += list(scheme_ids)
        sql += " ORDER BY s LIMIT ?"
        args.append(k)
        try:
            rows = self.db.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []                         # a query FTS cannot parse yields nothing, never an exception
        return [(cid, -s) for cid, s in rows]
