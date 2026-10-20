"""Tests for reading a schema out of a SQLite file.

Run: uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest test_sqlite.py -q

The fourth schema source, beside `.sql` DDL, SQL in string literals and SQLAlchemy
models. It is the only one that reads a BINARY file, and the only one whose answer
is the database's own account of itself rather than a reading of source.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
import srlib.schema as schema
from srlib.walk import classify, walk


def _database(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE hubs (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT
        );
        CREATE TABLE people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hub_id INTEGER NOT NULL REFERENCES hubs(id),
            full_name TEXT NOT NULL
        );
        CREATE VIEW active_people AS SELECT * FROM people;
        """
    )
    con.commit()
    con.close()
    return path


# ------------------------------------------------------------- 1. detector ---


def test_a_sqlite_file_yields_its_tables(tmp_path):
    found, unread = schema.detect_sqlite(_database(tmp_path / "app.db"))
    assert [t["name"] for t in found] == ["hubs", "people"]
    assert unread == []


def test_a_column_carries_its_declared_type_and_primary_key(tmp_path):
    found, _ = schema.detect_sqlite(_database(tmp_path / "app.db"))
    hubs = next(t for t in found if t["name"] == "hubs")
    assert [(c["name"], c["type"], c["pk"]) for c in hubs["columns"]] == [
        ("id", "INTEGER", True),
        ("code", "TEXT", False),
        ("name", "TEXT", False),
    ]


def test_a_foreign_key_points_at_the_column_it_references(tmp_path):
    found, _ = schema.detect_sqlite(_database(tmp_path / "app.db"))
    people = next(t for t in found if t["name"] == "people")
    fk = next(c for c in people["columns"] if c["name"] == "hub_id")
    assert fk["fk"] == "hubs.id"


def test_nullability_is_read_because_the_database_states_it(tmp_path):
    """Unlike a DDL reading, this one never has to guess: `PRAGMA table_info`
    answers for every column."""
    found, _ = schema.detect_sqlite(_database(tmp_path / "app.db"))
    people = next(t for t in found if t["name"] == "people")
    by_name = {c["name"]: c for c in people["columns"]}
    assert by_name["full_name"]["nullable"] is False
    hubs = next(t for t in found if t["name"] == "hubs")
    assert {c["name"]: c["nullable"] for c in hubs["columns"]}["name"] is True


def test_a_view_is_not_reported_as_a_table(tmp_path):
    """A view is a saved query, not a place rows live. Drawing it beside the tables
    would promise storage that is not there."""
    found, _ = schema.detect_sqlite(_database(tmp_path / "app.db"))
    assert "active_people" not in {t["name"] for t in found}


def test_sqlites_own_bookkeeping_tables_stay_out(tmp_path):
    """AUTOINCREMENT creates `sqlite_sequence`, which is the engine's, not the app's."""
    found, _ = schema.detect_sqlite(_database(tmp_path / "app.db"))
    assert not any(t["name"].startswith("sqlite_") for t in found)


def test_a_file_that_is_not_a_database_is_reported_rather_than_opened(tmp_path):
    junk = tmp_path / "notes.db"
    junk.write_bytes(b"this is a text file somebody named .db\n")
    found, unread = schema.detect_sqlite(junk)
    assert found == []
    assert unread and "not a SQLite" in unread[0]["why"]


def test_an_empty_database_reports_nothing_and_does_not_fail(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    assert schema.detect_sqlite(path) == ([], [])


def test_a_table_name_with_a_quote_in_it_does_not_break_the_pragma(tmp_path):
    path = tmp_path / "odd.db"
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "we""ird" (id INTEGER PRIMARY KEY)')
    con.commit()
    con.close()
    found, _ = schema.detect_sqlite(path)
    assert [t["name"] for t in found] == ['we"ird']


# ----------------------------------------------------------------- 2. walk ---


def test_a_database_file_is_classified_rather_than_skipped():
    for name in ("app.db", "store.sqlite", "cache.sqlite3"):
        kind, _lang, category = classify(Path(name))
        assert (kind, category) == ("non_code", "database"), name


def test_a_database_bigger_than_the_text_limit_is_still_walked(tmp_path):
    """The size cap exists because a megabyte of TEXT is not worth parsing. A
    database is read through its header pages, so its size says nothing — and real
    ones are big: the first `.db` this was tried on was 102 MB.
    """
    path = _database(tmp_path / "big.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE padding (id INTEGER PRIMARY KEY, blob TEXT)")
    con.executemany(
        "INSERT INTO padding (blob) VALUES (?)", [("x" * 4000,) for _ in range(400)]
    )
    con.commit()
    con.close()
    assert path.stat().st_size > 1_000_000
    assert "big.db" in {entry.path for entry in walk(tmp_path)}


def test_a_database_file_is_not_hashed_by_reading_all_of_it(tmp_path):
    """Walk hashes file bytes. On a 102 MB database that is a lot of reading for a
    question about its schema, so the schema hashes itself later instead."""
    _database(tmp_path / "app.db")
    entry = next(e for e in walk(tmp_path) if e.path == "app.db")
    assert entry.sha == ""


# --------------------------------------------------------------- 3. render ---


def _repo(tmp_path):
    (tmp_path / "app.py").write_text("import sqlite3\n\ndef go():\n    return 1\n")
    _database(tmp_path / "roster.db")
    return tmp_path


def test_plan_reads_the_schema_out_of_a_database_file(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    import json
    facts = json.loads((out / ".soulreap" / "facts.json").read_text())["files"]
    assert "roster.db" in facts
    assert [t["name"] for t in facts["roster.db"]["schema"]["tables"]] == ["hubs", "people"]
    assert facts["roster.db"]["schema"]["tables"][0]["detector"] == "sqlite_file"


def test_the_schema_of_a_database_file_is_hashed_from_the_schema(tmp_path):
    """So a render is dirty when the shape changed, not when a row was inserted."""
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    import json
    first = json.loads((out / ".soulreap" / "facts.json").read_text())["files"]["roster.db"]["sha"]
    con = sqlite3.connect(tmp_path / "roster.db")
    con.execute("INSERT INTO hubs (code, name) VALUES ('a', 'A')")
    con.commit()
    con.close()
    sr.cmd_plan(root=tmp_path, out=out)
    second = json.loads((out / ".soulreap" / "facts.json").read_text())["files"]["roster.db"]["sha"]
    assert first and first == second


def test_render_draws_an_erd_named_after_the_database_file(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    assert stats["schema_tables"] == 2
    assert (out / "diagrams" / "erd-roster.svg").exists()
    assert (out / "diagrams" / "erd-roster.svg").exists()


def test_the_erd_says_the_schema_came_out_of_the_database_itself(tmp_path):
    """A reader has to be able to tell a promise from a fact: a migration is what
    somebody meant to build, a database file is what is actually there."""
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    drawing = (out / "diagrams" / "erd-roster.svg").read_text()
    assert "read from the database file" in drawing
