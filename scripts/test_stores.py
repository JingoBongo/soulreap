"""Tests for observed stores: a named container whose shape is read off its uses.

Run: uv run --with pytest --with pygraphviz --with pathspec \
         --with tree-sitter-language-pack pytest test_stores.py -q

The odd one out among the schema sources. DDL, a model and a database file all
DECLARE; this one collects traces, and every test below exists to keep that
difference visible — in what is detected, in what is admitted as unreadable, and in
how the picture is captioned.
"""

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import soulreap as sr
from srlib.stores import detect_stores

VAULT = '''
from vaults import Vault

repos = Vault("remote_repositories")

def show(name):
    record = repos.get(name)
    return record["status"], record["PID"]

def launch(name):
    record = repos.get(name)
    record["status"] = "running"
    record["instructions"] = []
    repos.put(name, record)
'''


def _stores(source: str):
    return detect_stores(ast.parse(source))


# ------------------------------------------------------------- 1. the store ---


def test_a_store_is_named_by_the_literal_it_was_built_with():
    found = _stores(VAULT)
    assert [s["name"] for s in found] == ["remote_repositories"]
    assert found[0]["binding"] == "repos"
    assert found[0]["ctor"] == "Vault"


def test_the_fields_are_the_string_keys_used_on_what_comes_out_of_it():
    columns = {c["name"] for c in _stores(VAULT)[0]["columns"]}
    assert columns == {"status", "PID", "instructions"}


def test_a_field_carries_how_often_it_was_read_and_written():
    by_name = {c["name"]: c for c in _stores(VAULT)[0]["columns"]}
    assert by_name["status"]["reads"] == 1 and by_name["status"]["writes"] == 1
    assert by_name["PID"]["reads"] == 1 and by_name["PID"]["writes"] == 0


def test_a_key_read_from_a_variable_is_counted_as_unreadable():
    """The number that keeps the picture honest: it says how much was NOT seen."""
    source = VAULT + '''
def dynamic(name, field):
    record = repos.get(name)
    return record[field]
'''
    assert _stores(source)[0]["unreadable"] == 1


def test_a_store_built_inside_a_constructor_is_found_too():
    """A redis client lives in `self.client = redis.from_url(...)`, never at module
    level, and that was the whole recall gap of the first measurement."""
    source = '''
import redis

class Cache:
    def __init__(self):
        self.client = redis.Redis(host="localhost")

    def get_page(self, url):
        page = self.client.get(url)
        return page["title"]

    def put_page(self, url, page):
        self.client.set(url, page)
'''
    found = _stores(source)
    assert [s["binding"] for s in found] == ["self.client"]
    assert {c["name"] for c in found[0]["columns"]} == {"title"}


def test_an_injected_client_with_a_default_is_still_found():
    """`self.client = client or redis.from_url(...)` — an injected dependency with a
    fallback. One of the two real caches measured is written exactly this way, and
    reading only a bare call missed it completely."""
    source = '''
import redis

class Cache:
    def __init__(self, client=None):
        self.client = client or redis.from_url("redis://localhost")

    def read(self, url):
        return self.client.get(f"page:{url}")

    def write(self, url, page):
        self.client.setex(f"page:{url}", 60, page)
'''
    found = _stores(source)
    assert [s["ctor"] for s in found] == ["redis.from_url"]
    assert [k["name"] for k in found[0]["keys"]] == ["page:"]


def test_a_store_with_no_literal_name_is_called_by_its_binding():
    source = '''
from vaults import Vault
cache = Vault()

def use(k):
    row = cache.get(k)
    row["seen"] = 1
    cache.put(k, row)
'''
    assert _stores(source)[0]["name"] == "cache"


# -------------------------------------------------------- 2. what is not one ---


def test_a_fastapi_router_is_not_a_store():
    """`.get()`, `.put()` and `.delete()` on a router are routing. 33 of the 57
    candidates the verb rule alone produced were this."""
    source = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/items")
def read():
    return []

@router.put("/items")
def write():
    return []

@router.delete("/items")
def drop():
    return []
'''
    assert _stores(source) == []


def test_an_in_process_standard_library_object_is_not_a_store():
    source = '''
import contextvars
current = contextvars.ContextVar("current")

def read():
    return current.get()

def write(value):
    current.set(value)
'''
    assert _stores(source) == []


def test_something_only_read_is_not_a_store():
    """A response or a fixture: nothing in this file owns it."""
    source = '''
import requests
data = requests.get("https://example.com").json()

def a():
    return data.get("one")

def b():
    return data.get("two")
'''
    assert _stores(source) == []


def test_an_in_process_queue_is_not_a_store():
    """`put` and `get`, and neither a key nor a field: an in-process queue belongs to
    the broker question — who publishes, who consumes — which is a topology and not a
    schema. The one false positive the finished detector produced over 2845 files."""
    source = '''
import queue
inbox = queue.Queue()

def send(item):
    inbox.put(item)

def take():
    return inbox.get()
'''
    assert _stores(source) == []


def test_something_only_written_is_not_a_store():
    source = '''
from logs import Sink
sink = Sink("audit")

def one():
    sink.write("a")

def two():
    sink.write("b")
'''
    assert _stores(source) == []


def test_a_store_nobody_reads_fields_out_of_is_still_a_store():
    """fuse_core's `venvs_vault` holds a path per key, not a record. It has no fields
    and it is still a place data lives."""
    source = '''
from vaults import Vault
venvs = Vault("virtual_environments")

def add(name, path):
    venvs.put(name, path)

def find(name):
    return venvs.get(name)
'''
    found = _stores(source)
    assert [s["name"] for s in found] == ["virtual_environments"]
    assert found[0]["columns"] == []


# ------------------------------------------------------------ 3. namespaces ---

CACHE = '''
import redis

SOURCES_TTL = 3600

class Cache:
    def __init__(self):
        self.client = redis.from_url("redis://localhost")

    def get_sources(self, claim):
        key = f"perplexity:{self._hash(claim)}"
        return self.client.get(key)

    def set_sources(self, claim, sources):
        key = f"perplexity:{self._hash(claim)}"
        self.client.setex(key, SOURCES_TTL, sources)

    def get_page(self, url):
        key = f"page_analysis:{self._hash(url)}"
        return self.client.get(key)

    def set_page(self, url, page):
        self.client.setex(f"page_analysis:{self._hash(url)}", 600, page)

    def config(self):
        return self.client.get("settings")
'''


def test_a_cache_reports_the_namespaces_its_keys_are_built_from():
    """A redis client has no fields at all — its keys are a prefix and a computed
    tail. The prefixes ARE the shape, and they are the whole answer to "what does
    this cache hold"."""
    store = _stores(CACHE)[0]
    assert store["columns"] == []
    assert [k["name"] for k in store["keys"]] == [
        "page_analysis:", "perplexity:", "settings"
    ]


def test_a_whole_literal_key_is_told_apart_from_a_prefix():
    """`get("settings")` is one key. `f"perplexity:{x}"` is a family of them, and
    saying they are the same thing would misreport both."""
    by_name = {k["name"]: k for k in _stores(CACHE)[0]["keys"]}
    assert by_name["settings"]["kind"] == "key"
    assert by_name["perplexity:"]["kind"] == "prefix"


def test_a_key_given_straight_to_the_call_is_collected_too():
    """Not every key goes through a local first."""
    assert "page_analysis:" in {k["name"] for k in _stores(CACHE)[0]["keys"]}


def test_a_namespace_carries_how_many_places_use_it():
    by_name = {k["name"]: k for k in _stores(CACHE)[0]["keys"]}
    assert by_name["perplexity:"]["uses"] == 2
    assert by_name["settings"]["uses"] == 1


def test_a_key_computed_at_run_time_is_stated_rather_than_mourned():
    """Not the same admission as an unreadable field. A record store is SUPPOSED to
    be keyed by a value — `remote_repositories` is keyed by a repository name — so
    this is a fact about the store, not a hole in the reading."""
    source = '''
from vaults import Vault
store = Vault("things")

def read(k):
    return store.get(k)

def write(k, v):
    store.put(k, v)
'''
    found = _stores(source)[0]
    assert found["keys"] == []
    assert found["dynamic_keys"] == 2
    assert found["unreadable"] == 0


def test_a_cache_with_namespaces_is_drawn_even_without_fields(tmp_path):
    """The hole this closes: before namespaces, a redis client came out with zero
    fields and therefore no picture, which read as "nothing found" for a cache that
    holds four things."""
    (tmp_path / "cache.py").write_text(CACHE)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    drawing = (out / "diagrams" / "observed-cache.svg").read_text()
    assert "perplexity:" in drawing
    assert "prefix" in drawing


# ------------------------------------------------------------- 4. the picture ---


def _repo(tmp_path):
    (tmp_path / "app.py").write_text(VAULT)
    return tmp_path


def test_plan_records_the_stores_it_observed(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    facts = json.loads((out / ".soulreap" / "facts.json").read_text())["files"]
    assert [s["name"] for s in facts["app.py"]["stores"]] == ["remote_repositories"]


def test_render_draws_the_observed_store(tmp_path):
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    assert stats["observed_stores"] == 1
    assert (out / "diagrams" / "observed-app.svg").exists()


def test_the_picture_says_the_schema_was_never_declared(tmp_path):
    """The requirement this whole feature is gated on. An observed schema looks as
    solid on a drawing as a DDL one, and only the caption says otherwise."""
    out = _repo(tmp_path) / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    drawing = (out / "diagrams" / "observed-app.svg").read_text()
    assert "nowhere declared" in drawing
    assert "observed" in drawing


def test_a_store_with_a_key_and_a_whole_value_is_drawn_key_to_value(tmp_path):
    """No field is read, but the store still has a shape: name → path. Drawing nothing (the
    rule until 2026-10-16) hid every record store whose value is kept whole."""
    (tmp_path / "app.py").write_text('''
from vaults import Vault
venvs = Vault("virtual_environments")

def add(name, path):
    venvs.put(name, path)

def find(name):
    return venvs.get(name)
''')
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    stats = sr.cmd_render(root=tmp_path, out=out)
    drawing = (out / "diagrams" / "observed-app.svg").read_text()
    assert "virtual_environments" in drawing and "name" in drawing and "path" in drawing
    assert "kept whole" in drawing and stats["observed_stores"] == 1


def test_a_stale_observed_picture_is_removed(tmp_path):
    out = tmp_path / "soulreap"
    (out / "diagrams").mkdir(parents=True)
    (out / "diagrams" / "observed-gone.svg").write_text("<svg/>")
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    assert not (out / "diagrams" / "observed-gone.svg").exists()


def test_an_unreadable_field_is_reported_on_the_picture(tmp_path):
    (tmp_path / "app.py").write_text(VAULT + '''
def dynamic(name, field):
    record = repos.get(name)
    return record[field]
''')
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    drawing = (out / "diagrams" / "observed-app.svg").read_text()
    assert "1 field could not be read" in drawing
    # and the computed key is named, with the function that sets it
    assert "set in launch" in drawing


def test_an_observed_store_never_lands_on_a_database_diagram(tmp_path):
    """Two frames, never one: a reader has to see the difference between a contract
    and a trace, and sharing a canvas is exactly how that is lost."""
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT);\n"
    )
    (tmp_path / "app.py").write_text(VAULT)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    sr.cmd_render(root=tmp_path, out=out)
    erd = next((out / "diagrams").glob("erd-*.svg")).read_text()
    assert "remote_repositories" not in erd
    assert (out / "diagrams" / "observed-app.svg").exists()


# ----------------------------------------------------------- 5. the skeleton ---


def test_the_skeleton_tells_a_carder_about_the_stores(tmp_path):
    """The gap this closes: for `fuse.py` the facts were collected and the diagram was
    drawn, and the writer of the card was never told the file owns four stores."""
    (tmp_path / "app.py").write_text(VAULT)
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    text = sr.cmd_skeleton(root=tmp_path, out=out, target="app.py")
    assert "## Stores" in text
    assert "remote_repositories" in text
    assert "OBSERVED" in text                       # never reads as a contract
    assert "diagrams/observed-app.svg" in text
    assert "field status" in text


def test_the_skeleton_says_nothing_about_stores_when_there_are_none(tmp_path):
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    out = tmp_path / "soulreap"
    sr.cmd_plan(root=tmp_path, out=out)
    assert "## Stores" not in sr.cmd_skeleton(root=tmp_path, out=out, target="app.py")


def test_fields_read_with_get_and_written_as_a_dict_literal_are_observed():
    source = (
        "import vaults\n\n\nclass UpdatesStore:\n"
        "    def __init__(self):\n        self._vault = vaults.Vault('mercy_auto_update')\n"
        "        for key in self._vault.list_keys():\n"
        "            entry = self._vault.get(key)\n"
        "            self.cache = bool(entry.get('enabled', False))\n\n"
        "    def set_enabled(self, name, on):\n        self._vault.put(name, {'enabled': bool(on)})\n\n"
        "    def remove(self, name):\n        self._vault.pop(name)\n"
    )
    (store,) = detect_stores(ast.parse(source))
    (column,) = store["columns"]
    assert (column["name"], column["reads"], column["writes"]) == ("enabled", 1, 1)


def test_a_store_named_through_a_module_constant_takes_that_name():
    source = ("import vaults\n\nVAULT_NAME = 'mercy_auto_update'\n\n\nclass S:\n"
              "    def __init__(self):\n        self._vault = vaults.Vault(VAULT_NAME)\n\n"
              "    def a(self, k):\n        return self._vault.get(k)\n\n"
              "    def b(self, k):\n        self._vault.put(k, {'x': 1})\n")
    (store,) = detect_stores(ast.parse(source))
    assert store["name"] == "mercy_auto_update"


def test_only_a_store_from_an_outside_library_is_said_to_outlive_the_process(tmp_path):
    from srlib.stores import store_library
    blob = {"imports": [{"module": "vaults", "names": ["Vault"], "target": None},
                        {"module": "cache", "names": ["Cache"], "target": "cache.py"}]}
    assert store_library(blob, {"ctor": "Vault"}) == "vaults"
    assert store_library(blob, {"ctor": "vaults.Vault"}) == "vaults"
    assert store_library(blob, {"ctor": "Cache"}) is None
    assert store_library({"imports": []}, {"ctor": "LocalCache"}) is None


MERCY_VAULT = (
    "import vaults\n\nVAULT_NAME = 'mercy_auto_update'\n\n\nclass UpdatesStore:\n"
    "    def __init__(self):\n        self._vault = vaults.Vault(VAULT_NAME)\n"
    "        for key in self._vault.list_keys():\n            entry = self._vault.get(key)\n"
    "            self.c = bool(entry.get('enabled', False))\n\n"
    "    def set_enabled(self, name, on):\n        self._vault.put(name, {'enabled': bool(on)})\n\n"
    "    def remove(self, name):\n        self._vault.pop(name)\n"
)


def test_a_field_written_as_a_literal_takes_the_type_the_code_gives_it():
    (store,) = detect_stores(ast.parse(MERCY_VAULT))
    assert store["columns"][0]["type"] == "bool"


def test_a_computed_key_is_described_by_its_variable_type_writers_and_removers():
    (store,) = detect_stores(ast.parse(MERCY_VAULT.replace("def set_enabled(self, name, on)",
                                                            "def set_enabled(self, name: str, on)")))
    assert store["key"] == {"names": ["name", "key"], "type": "str",
                            "writers": ["set_enabled"], "removers": ["remove"], "values": []}


def test_a_store_keyed_only_by_literals_has_no_computed_key():
    source = ("import redis\n\n\nclass C:\n    def __init__(self):\n        self.r = redis.Redis()\n\n"
              "    def a(self):\n        return self.r.get('settings')\n\n"
              "    def b(self, v):\n        self.r.set('settings', v)\n")
    (store,) = detect_stores(ast.parse(source))
    assert store["key"] is None and [k["name"] for k in store["keys"]] == ["settings"]


def test_the_store_picture_says_what_the_store_is_and_stays_compact(tmp_path):
    from srlib.build import build_facts
    from srlib.erd_dot_observed import observed_dot_svg, observed_groups
    (tmp_path / "updates_store.py").write_text(MERCY_VAULT, encoding="utf-8")
    (group,) = observed_groups(build_facts(tmp_path).files).values()
    svg = observed_dot_svg(group)
    if svg is None:  # no Graphviz here
        return
    for text in ("mercy_auto_update", "vaults", "outside the process", "name", "set_enabled", "remove",
                 "enabled", "bool"):
        assert text in svg, text
    assert "1760" not in svg
    width = float(svg.split('width="', 1)[1].split("pt", 1)[0])
    assert width < 700
