"""Tests for declared in-memory structures: class fields, their detector, their diagram.

Run: uv run --with pytest --with pygraphviz --with pathspec --with tree-sitter-language-pack \
         pytest test_structures.py -q

Kept apart from test_soulreap.py on purpose: this covers one seam — a class body read
as a shape (dataclass, TypedDict, NamedTuple, pydantic model) rather than as a place
where methods live — and the rule that such a shape is never drawn on a database ERD.
"""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
from srlib import erd_dot
from srlib import schema as sr_schema

# --------------------------------------------------------------------------
# Step 0: a class body has fields, and the parser has to write them down
# --------------------------------------------------------------------------

FIELDS_SOURCE = '''
from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    """How the server binds."""

    host: str = "0.0.0.0"
    port: int = 4053
    api_token: str = "hunter2"
    banner: str = "https://example.com/hello?token=abc123"
    owner: str
'''


def test_class_fields_are_collected_with_name_type_default_and_line():
    facts = sr.extract_python(FIELDS_SOURCE, "config.py")
    fields = {f["name"]: f for f in facts.classes[0]["fields"]}

    assert fields["host"]["type"] == "str"
    assert fields["host"]["default"] == "'0.0.0.0'"
    assert fields["port"]["line"] == FIELDS_SOURCE.splitlines().index("    port: int = 4053") + 1


def test_a_field_with_no_default_reports_none_rather_than_an_empty_string():
    facts = sr.extract_python(FIELDS_SOURCE, "config.py")
    fields = {f["name"]: f for f in facts.classes[0]["fields"]}

    assert fields["owner"]["default"] is None


def test_a_field_whose_name_looks_like_a_credential_is_redacted():
    """Same rule module constants already live under — a default is source text too."""
    facts = sr.extract_python(FIELDS_SOURCE, "config.py")
    fields = {f["name"]: f for f in facts.classes[0]["fields"]}

    assert fields["api_token"]["default"] == "<redacted>"
    assert "token=abc123" not in fields["banner"]["default"]


def test_a_class_with_no_annotated_attributes_carries_an_empty_field_list():
    source = "class Plain:\n    def run(self):\n        return 1\n"
    facts = sr.extract_python(source, "plain.py")

    assert facts.classes[0]["fields"] == []


def test_the_skeleton_prints_class_fields_under_their_class(tmp_path, capsys):
    """Entities was the emptiest part of a dataclass card: a name and nothing else."""
    (tmp_path / "config.py").write_text(FIELDS_SOURCE)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_skeleton(root=tmp_path, out=out, target="config.py")
    printed = capsys.readouterr().out

    assert "host: str = '0.0.0.0'" in printed
    assert "port: int = 4053" in printed


# --------------------------------------------------------------------------
# Step 1: the detector — dataclass, TypedDict, NamedTuple, pydantic
# --------------------------------------------------------------------------

STRUCTURES_SOURCE = '''
from dataclasses import dataclass, field
from typing import NamedTuple, NotRequired, Optional, TypedDict

from pydantic import BaseModel, Field


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 4053


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    workers: list[ServerConfig] = field(default_factory=list)
    routes: dict[str, list[LoggingConfig]] = field(default_factory=dict)
    fallback: Optional[ServerConfig] = None
    name: str
    tag: str = field()


class Point(NamedTuple):
    x: int
    y: int = 0


class Payload(TypedDict):
    key: str
    extra: NotRequired[str]


class Loose(TypedDict, total=False):
    maybe: str


class User(BaseModel):
    id: int = Field(...)
    home: "ServerConfig" = None
    nickname: str = "anon"


class Admin(User):
    scopes: list[str] = []


class NotAStructure:
    def run(self) -> None:
        return None
'''


def _structures(source: str = STRUCTURES_SOURCE) -> dict[str, dict]:
    found, _ = sr_schema.detect_structures(ast.parse(source), source)
    return {item["name"]: item for item in found}


def _columns(structure: dict) -> dict[str, dict]:
    return {column["name"]: column for column in structure["columns"]}


def test_the_detector_returns_the_same_shape_the_table_detectors_do():
    config = _structures()["Config"]

    assert set(config) >= {"name", "columns", "line", "end_line", "detector"}
    assert config["detector"] == "dataclass"
    assert config["line"] < config["end_line"]


def test_every_declared_structure_family_is_detected_and_plain_classes_are_not():
    found = _structures()

    assert found["Point"]["detector"] == "named_tuple"
    assert found["Payload"]["detector"] == "typed_dict"
    assert found["User"]["detector"] == "pydantic"
    assert "NotAStructure" not in found


def test_a_subclass_of_a_local_structure_is_a_structure_too():
    """`class Admin(User)` inherits a declaration, it does not weaken one."""
    assert _structures()["Admin"]["detector"] == "pydantic"


def test_a_field_typed_as_another_structure_is_a_reference_to_it():
    server = _columns(_structures()["Config"])["server"]

    assert server["fk"] == "ServerConfig"
    assert server["many"] is False


def test_a_list_of_structures_is_the_same_reference_but_many():
    workers = _columns(_structures()["Config"])["workers"]

    assert workers["fk"] == "ServerConfig"
    assert workers["many"] is True


def test_a_nested_container_is_many_and_keeps_the_real_type_written_out():
    """`dict[str, list[LoggingConfig]]` — cardinality is two-deep; the type says so."""
    routes = _columns(_structures()["Config"])["routes"]

    assert routes["fk"] == "LoggingConfig"
    assert routes["many"] is True
    assert routes["type"] == "dict[str, list[LoggingConfig]]"


def test_an_optional_reference_is_a_reference_and_is_nullable():
    fallback = _columns(_structures()["Config"])["fallback"]

    assert fallback["fk"] == "ServerConfig"
    assert fallback["many"] is False
    assert fallback["nullable"] is True
    assert _columns(_structures()["Config"])["server"]["nullable"] is False


def test_a_string_forward_reference_resolves_to_the_structure_it_names():
    assert _columns(_structures()["User"])["home"]["fk"] == "ServerConfig"


def test_a_type_declared_in_another_module_is_never_invented_into_a_reference():
    source = (
        "from dataclasses import dataclass\n"
        "from elsewhere import Person\n\n"
        "@dataclass\nclass Team:\n    lead: Person\n"
    )
    assert _columns(_structures(source)["Team"])["lead"]["fk"] is None


def test_a_structure_never_carries_a_primary_key():
    """There is no such thing in a class body; a PK tag here would be invented."""
    assert all(not column["pk"] for item in _structures().values() for column in item["columns"])


def test_a_field_with_no_default_is_required_and_one_with_a_default_is_not():
    config = _columns(_structures()["Config"])

    assert config["name"]["required"] is True
    assert config["server"]["required"] is False


def test_a_bare_field_call_declares_no_default_and_stays_required():
    """`tag: str = field()` looks like a default and is not one — dataclasses raises."""
    assert _columns(_structures()["Config"])["tag"]["required"] is True


def test_pydantic_ellipsis_is_required_and_a_literal_default_is_not():
    user = _columns(_structures()["User"])

    assert user["id"]["required"] is True
    assert user["nickname"]["required"] is False


def test_typed_dict_keys_are_required_unless_declared_otherwise():
    found = _structures()

    assert _columns(found["Payload"])["key"]["required"] is True
    assert _columns(found["Payload"])["extra"]["required"] is False
    assert _columns(found["Loose"])["maybe"]["required"] is False


def test_an_orm_model_is_a_table_and_is_never_reported_as_a_structure():
    """The hard rule: a real table must not be drawn twice, in two languages."""
    source = (
        "from pydantic import BaseModel\n\n"
        "class Job(BaseModel):\n"
        "    __tablename__ = 'jobs'\n"
        "    id: int\n"
    )
    assert _structures(source) == {}


def test_a_class_variable_is_not_a_field_of_the_structure():
    source = (
        "from dataclasses import dataclass\n"
        "from typing import ClassVar\n\n"
        "@dataclass\nclass Box:\n    kind: ClassVar[str] = 'box'\n    size: int = 1\n"
    )
    assert list(_columns(_structures(source)["Box"])) == ["size"]


def test_python_facts_carry_the_structures_a_file_declares():
    facts = sr.extract_python(STRUCTURES_SOURCE, "models.py")

    assert {item["name"] for item in facts.schema["structures"]} >= {"Config", "Point", "User"}


def test_structures_never_leak_into_the_table_list():
    """`group_databases` reads `tables`; a dataclass there would join a real ERD."""
    facts = sr.extract_python(STRUCTURES_SOURCE, "models.py")

    assert facts.schema["tables"] == []


# --------------------------------------------------------------------------
# Step 2: the picture — one per module, and never the database's picture
# --------------------------------------------------------------------------


def _facts_with_structures(path: str = "config.py") -> dict:
    blob = sr.extract_python(STRUCTURES_SOURCE, path)
    return {path: blob.to_dict()}


def test_only_structures_linked_to_another_structure_reach_the_diagram():
    """Two hundred unrelated DTO boxes is not a picture. The card keeps the singletons."""
    group = erd_dot.structure_groups(_facts_with_structures())["config.py"]
    drawn = {item["name"] for item in group["structures"]}

    assert drawn == {"Config", "ServerConfig", "LoggingConfig", "User"}
    assert "Point" not in drawn


def test_a_module_whose_structures_point_at_nothing_gets_no_diagram():
    source = "from dataclasses import dataclass\n\n@dataclass\nclass Lonely:\n    n: int = 0\n"
    facts = {"dto.py": sr.extract_python(source, "dto.py").to_dict()}

    assert erd_dot.structure_groups(facts) == {}


def test_the_slug_is_the_module_path_so_two_modules_cannot_collide():
    assert erd_dot.structure_slug("app/models/user.py") == "app-models-user"
    assert erd_dot.structure_slug("config.py") == "config"


def test_the_drawing_carries_every_linked_structure_and_its_fields():
    pytest.importorskip("pygraphviz")
    group = erd_dot.structure_groups(_facts_with_structures())["config.py"]
    svg = erd_dot.structures_dot_svg(group)

    assert svg is not None
    for name in ("Config", "ServerConfig", "LoggingConfig", "host", "port", "workers"):
        assert name in svg, f"{name} is missing from the drawing"


def test_the_drawing_marks_a_field_that_has_no_default():
    pytest.importorskip("pygraphviz")
    group = erd_dot.structure_groups(_facts_with_structures())["config.py"]

    assert "req" in erd_dot.structures_dot_svg(group)


def test_the_caption_says_these_are_not_database_tables():
    """A structure diagram that reads like a schema is the false confidence risk."""
    pytest.importorskip("pygraphviz")
    group = erd_dot.structure_groups(_facts_with_structures())["config.py"]
    svg = erd_dot.structures_dot_svg(group)

    assert "not database tables" in svg
    assert "config.py" in svg


def test_a_wrapped_reference_shows_the_real_type_it_was_declared_with():
    """A crow's foot says "many" and stops; two containers deep has to be written out.

    In the field row, not on the edge: an edge label together with the graph's
    `ratio` aborts Graphviz (SIGSEGV), which is how this landed in the box."""
    pytest.importorskip("pygraphviz")
    group = erd_dot.structure_groups(_facts_with_structures())["config.py"]
    svg = erd_dot.structures_dot_svg(group)

    assert "dict[str, list[LoggingConfig]]" in svg


def test_a_very_long_annotation_is_shortened_in_the_field_row():
    """One `Annotated[...]` constraint doubled a box's width on a real module."""
    pytest.importorskip("pygraphviz")
    long_type = "Annotated[str, StringConstraints(strip_whitespace=True, min_length=3)]"
    source = (
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass Inner:\n    n: int = 0\n\n"
        f"@dataclass\nclass Outer:\n    inner: Inner = None\n    long: {long_type} = 'x'\n"
    )
    facts = {"m.py": sr.extract_python(source, "m.py").to_dict()}
    svg = erd_dot.structures_dot_svg(erd_dot.structure_groups(facts)["m.py"])

    assert long_type not in svg
    assert "Annotated[str, StringConstraints(strip" in svg
    assert "…" in svg


def test_the_drawing_is_skipped_without_graphviz(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "pygraphviz":
            raise ImportError("no wheel here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    group = erd_dot.structure_groups(_facts_with_structures())["config.py"]
    assert erd_dot.structures_dot_svg(group) is None


# --------------------------------------------------------------------------
# Step 2, wired: render writes the file, and keeps it away from the ERD
# --------------------------------------------------------------------------

ORM_SOURCE = '''
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    body = Column(String(200))
'''


@pytest.fixture
def mixed_repo(tmp_path):
    (tmp_path / "db.py").write_text(ORM_SOURCE)
    (tmp_path / "config.py").write_text(STRUCTURES_SOURCE)
    return tmp_path


def test_render_writes_one_structures_diagram_per_declaring_module(mixed_repo):
    pytest.importorskip("pygraphviz")
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    result = sr.cmd_render(root=mixed_repo, out=out)

    assert (out / "diagrams" / "structures-config.svg").exists()
    assert result["structure_diagrams"] == 1


def test_a_structure_is_never_drawn_on_the_database_diagram(mixed_repo):
    pytest.importorskip("pygraphviz")
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_render(root=mixed_repo, out=out)

    for erd in (out / "diagrams").glob("erd-*"):
        assert "ServerConfig" not in erd.read_text(), f"{erd.name} mixes a class into a schema"


def test_a_structures_diagram_of_a_module_that_lost_its_structures_is_removed(mixed_repo):
    """Same rule the ERD follows: a picture of code that is gone reads as live."""
    pytest.importorskip("pygraphviz")
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_render(root=mixed_repo, out=out)

    (mixed_repo / "config.py").write_text("VALUE = 1\n")
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_render(root=mixed_repo, out=out)

    assert not (out / "diagrams" / "structures-config.svg").exists()


def test_the_skeleton_points_the_carder_at_the_structures_diagram(mixed_repo, capsys):
    pytest.importorskip("pygraphviz")
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_skeleton(root=mixed_repo, out=out, target="config.py")
    printed = capsys.readouterr().out

    assert "## Structures" in printed
    assert "diagrams/structures-config.svg" in printed
    assert "dataclass Config" in printed
    assert "server: ServerConfig -> ServerConfig" in printed
    assert "workers: list[ServerConfig] -> ServerConfig (many)" in printed
    assert "name: str required" in printed


def test_the_structures_section_repeats_no_field_the_entities_table_already_has(
    mixed_repo, capsys
):
    """A plain field with a default is printed once, above, with its annotation."""
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_skeleton(root=mixed_repo, out=out, target="config.py")
    printed = capsys.readouterr().out
    section = printed.split("## Structures", 1)[1]

    assert "nickname" not in section
    assert "nickname: str = 'anon'" in printed


def test_the_skeleton_says_a_structure_is_not_a_table(mixed_repo, capsys):
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_skeleton(root=mixed_repo, out=out, target="config.py")
    printed = capsys.readouterr().out

    assert "not database tables" in printed
    assert "## Schema" not in printed


def test_a_file_declaring_only_tables_prints_no_structures_section(mixed_repo, capsys):
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    sr.cmd_skeleton(root=mixed_repo, out=out, target="db.py")
    printed = capsys.readouterr().out

    assert "## Structures" not in printed
    assert "## Schema" in printed


def test_structures_are_reported_even_when_no_diagram_was_drawn(tmp_path, capsys):
    """A singleton structure is off the picture on purpose — the card still names it."""
    source = "from dataclasses import dataclass\n\n@dataclass\nclass Lonely:\n    n: int = 0\n"
    (tmp_path / "dto.py").write_text(source)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_skeleton(root=tmp_path, out=out, target="dto.py")
    printed = capsys.readouterr().out

    assert "dataclass Lonely" in printed
    assert "structures-dto.svg" not in printed


def test_render_counts_the_structures_it_drew(mixed_repo):
    pytest.importorskip("pygraphviz")
    out = mixed_repo / "soulreap"
    sr.cmd_plan(root=mixed_repo, out=out)
    result = sr.cmd_render(root=mixed_repo, out=out, diagrams="all")

    assert result["structures_drawn"] == 4
