"""Diagrams draw what matters: no ERD per mixin, none from a test's scratch table, stores shown."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib import overview
from srlib.diagrams import group_databases
from srlib.schema_orm import detect_sqlalchemy

MODELS = (
    "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n\n\n"
    "class Base(DeclarativeBase):\n    pass\n\n\n"
    "class _IdMixin:\n    id: Mapped[int] = mapped_column(primary_key=True)\n\n\n"
    "class Label(_IdMixin, Base):\n    __tablename__ = 'labels'\n    name: Mapped[str] = mapped_column()\n\n\n"
    "class User(_IdMixin, Base):\n    __tablename__ = 'users'\n    email: Mapped[str] = mapped_column()\n"
)


def test_tables_behind_a_mixin_group_under_their_declarative_base():
    tables, _ = detect_sqlalchemy(ast.parse(MODELS), MODELS)
    assert {t["base"] for t in tables} == {"Base"}


def test_a_table_a_test_file_creates_is_not_a_database():
    facts = {"src/models.py": {"schema": {"tables": [{"name": "labels", "base": "Base", "columns": []}]}},
             "tests/test_rls.py": {"schema": {"tables": [{"name": "control.evil", "columns": []}]}}}
    assert [d["name"] for d in group_databases(facts).values()] == ["Base"]


def test_an_observed_store_diagram_is_pasted_not_only_named(tmp_path):
    (tmp_path / "observed-updates-store.svg").write_text("<svg/>", encoding="utf-8")
    assert '<img src="diagrams/observed-updates-store.svg"' in overview.diagrams(tmp_path)


def test_an_observed_store_is_a_system_on_the_context_diagram():
    from srlib.diagrams_context import _context_edges
    facts = {"updates_store.py": {"lang": "python", "imports": [{"module": "vaults", "names": []}], "io": {},
                                  "stores": [{"name": "_vault", "ctor": "vaults.Vault", "binding": "self._vault"}]}}
    _, systems, _ = _context_edges(facts)
    assert systems.get("vaults") == "key-value store"


def test_a_store_of_a_known_service_adds_no_second_node():
    from srlib.diagrams_context import _context_edges
    facts = {"cache.py": {"lang": "python", "imports": [{"module": "redis", "names": []}], "io": {},
                          "stores": [{"name": "client", "ctor": "redis.from_url", "binding": "self.client"}]}}
    _, systems, _ = _context_edges(facts)
    assert "redis" not in systems and len(systems) == 1


def test_a_class_of_this_repo_with_get_and_put_is_not_an_outside_system():
    from srlib.diagrams_context import _context_edges
    facts = {"svc.py": {"lang": "python", "imports": [{"module": "cache", "names": ["Cache"], "target": "cache.py"}],
                        "io": {}, "stores": [{"name": "c", "ctor": "Cache", "binding": "c"}]}}
    assert "Cache" not in _context_edges(facts)[1]
