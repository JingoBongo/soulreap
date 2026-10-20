"""Tests for per-symbol hazards and the structural fingerprint."""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.facts import FACTS_VERSION, extract_python
from srlib.hazards import fingerprint, hazards_of, io_aliases, module_constants, module_names


def _found(src: str, name: str | None = None) -> list[dict]:
    tree = ast.parse(textwrap.dedent(src))
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (name is None or n.name == name)
    )
    return hazards_of(node, module_names(tree), io_aliases(tree), module_constants(tree))


def _kinds(src: str, name: str | None = None) -> set[tuple[str, str]]:
    return {(h["kind"], h["what"]) for h in _found(src, name)}


def test_hazards_except_without_raise_is_swallow():
    assert ("swallow", "Exception") in _kinds("def f():\n try:\n  g()\n except Exception:\n  log()\n")


def test_hazards_bare_except_is_swallow():
    assert ("swallow", "bare except") in _kinds("def f():\n try:\n  g()\n except:\n  pass\n")


def test_hazards_except_that_reraises_is_not_swallow():
    src = "def f():\n try:\n  g()\n except ValueError as e:\n  raise RuntimeError() from e\n"
    assert not any(kind == "swallow" for kind, _ in _kinds(src))


def test_hazards_raise_names_the_exception_type():
    assert ("raise", "KeyError") in _kinds("def f():\n raise KeyError('x')\n")


def test_hazards_with_lock_and_acquire_are_locks():
    src = "def f(self):\n with self._lock:\n  pass\n self.sem.acquire()\n"
    assert {("lock", "self._lock"), ("lock", "self.sem")} <= _kinds(src)


def test_hazards_writes_self_attr_global_and_module_container():
    src = (
        "CACHE = {}\ncount = 0\n"
        "def f(self, k):\n global count\n self.x = 1\n CACHE[k] = 2\n count += 1\n self._items.append(k)\n"
    )
    assert {("write", "self.x"), ("write", "CACHE"), ("write", "count"), ("write", "self._items")} <= _kinds(src)


def test_hazards_init_self_assignments_are_not_writes():
    assert _kinds("class A:\n def __init__(self):\n  self.x = 1\n", "__init__") == set()


def test_hazards_external_calls_via_io_module_alias():
    src = "import requests as rq\nfrom subprocess import run\ndef f():\n rq.post('u')\n run(['ls'])\n"
    assert {("external", "rq.post"), ("external", "run")} <= _kinds(src)


def test_hazards_open_for_writing_and_sql_write_but_not_open_for_reading():
    src = "def f(db):\n open('a.txt', 'w')\n open('b.txt')\n db.execute('INSERT INTO users VALUES (?)')\n"
    kinds = _kinds(src)
    assert {("write", "open:w"), ("write", "sql:INSERT users")} <= kinds
    assert not any(what.startswith("open:r") for _, what in kinds)


def test_hazards_spawn_and_sleep_only_inside_a_loop():
    src = (
        "import threading, time\n"
        "def f():\n threading.Thread(target=g).start()\n while True:\n  time.sleep(1)\n time.sleep(2)\n"
    )
    found = _found(src)
    assert ("spawn", "threading.Thread") in {(h["kind"], h["what"]) for h in found}
    assert [h["line"] for h in found if h["kind"] == "sleep_retry"] == [5]


def test_hazards_of_nested_function_belong_to_the_enclosing_symbol():
    # Nested functions are not symbols of their own, so their hazards would otherwise be lost.
    src = "def f():\n def worker():\n  try:\n   g()\n  except Exception:\n   pass\n return worker\n"
    assert ("swallow", "Exception") in _kinds(src, "f")


def test_fingerprint_ignores_hazard_lines_but_not_callees():
    one = fingerprint("f()", ["g"], [{"kind": "raise", "what": "E", "line": 3}])
    assert one == fingerprint("f()", ["g"], [{"kind": "raise", "what": "E", "line": 9}])
    assert one != fingerprint("f()", ["g", "h"], [{"kind": "raise", "what": "E", "line": 3}])
    assert len(one) == 10


def test_extract_python_callable_carries_callees_hazards_and_sfp():
    base = extract_python("def f():\n    g()\n", "m.py").functions[0]
    assert base["callees"] == ["g"] and base["hazards"] == [] and len(base["sfp"]) == 10
    commented = extract_python("def f():\n    # why\n    g()\n", "m.py").functions[0]
    assert commented["sfp"] == base["sfp"]
    grown = extract_python("def f():\n    g()\n    h()\n", "m.py").functions[0]
    assert grown["sfp"] != base["sfp"]


def test_extract_python_methods_carry_sfp_too():
    cls = extract_python("class A:\n    def put(self):\n        self.v = 1\n", "m.py").classes[0]
    method = cls["methods"][0]
    assert ("write", "self.v") in {(h["kind"], h["what"]) for h in method["hazards"]}


def test_facts_version_is_eight():
    assert FACTS_VERSION == 8


def test_hazards_init_self_mutator_calls_are_not_writes():
    src = "class A:\n def __init__(self):\n  self._cache = {}\n  self._cache.update({'k': 1})\n"
    assert _kinds(src, "__init__") == set()


def test_hazards_init_self_subscript_assignment_is_not_a_write():
    src = "class A:\n def __init__(self):\n  self._c = {}\n  self._c['k'] = 1\n"
    assert _kinds(src, "__init__") == set()


def test_hazards_urllib_parse_is_not_external_but_urlopen_is():
    src = (
        "import urllib.parse\nimport urllib.request\nfrom urllib.parse import urlparse\n"
        "def f(p):\n urlparse(p)\n urllib.parse.quote(p)\n urllib.request.urlopen(p)\n"
    )
    assert {what for kind, what in _kinds(src) if kind == "external"} == {"urllib.request.urlopen"}


def test_hazards_external_call_carries_its_url_literal():
    src = (
        "import requests\n"
        "def f(base):\n"
        " requests.post(f'{base}/launch_process', timeout=5)\n"
        " requests.get('http://fuse:4053/api/services')\n"
        " requests.get(base)\n"
    )
    external = {what for kind, what in _kinds(src) if kind == "external"}
    assert external == {"requests.post {base}/launch_process", "requests.get http://fuse:4053/api/services",
                        "requests.get"}


def test_extract_python_callable_keeps_its_whole_docstring():
    src = 'def f():\n    """First line.\n\n    Keys outside EDITABLE are dropped.\n    """\n'
    fn = extract_python(src, "m.py").functions[0]
    assert fn["doc"] == "First line."
    assert "Keys outside EDITABLE are dropped." in fn["doc_more"]


LITERALS_SRC = (
    "import re, subprocess\n"
    "MARK = re.compile(r'<!--(.*)-->')\n"
    "def sync(p, a, b, branch):\n"
    "    '''Runs git status and friends.'''\n"
    "    subprocess.run(['git', 'merge-base', '--is-ancestor', a, b])\n"
    "    run_shell(f'git -C {p} reset --hard origin/{branch}')\n"
    "    return re.findall(r'^# fuse:(\\w+)=(.*)$', p, re.M)\n"
)


def test_literals_of_callable_are_its_commands_and_regexes():
    from srlib.fingerprints import literals_of

    tree = ast.parse(LITERALS_SRC)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    assert literals_of(node) == [
        {"kind": "command", "text": "git merge-base --is-ancestor {a} {b}", "line": 5},
        {"kind": "command", "text": "git -C {p} reset --hard origin/{branch}", "line": 6},
        {"kind": "regex", "text": "^# fuse:(\\w+)=(.*)$", "line": 7},
    ]


def test_fingerprint_moves_when_a_command_flag_changes():
    soft = extract_python("def f(p):\n    run(f'git -C {p} reset --soft x')\n", "m.py").functions[0]
    hard = extract_python("def f(p):\n    run(f'git -C {p} reset --hard x')\n", "m.py").functions[0]
    assert soft["literals"][0]["text"] == "git -C {p} reset --soft x"
    assert soft["sfp"] != hard["sfp"]


def test_literals_of_list_holding_an_fstring_command():
    from srlib.fingerprints import literals_of

    src = "def f(p, a):\n    run([f'git -C {p}'\n         f' merge-base --is-ancestor {a} HEAD'])\n"
    node = ast.parse(src).body[0]
    assert [lit["text"] for lit in literals_of(node)] == ["git -C {p} merge-base --is-ancestor {a} HEAD"]


def test_body_fingerprint_moves_on_values_not_on_comments_or_docstrings():
    def bfp(src: str) -> str:
        return next(f for f in extract_python(src, "m.py").functions if f["name"] == "f")["bfp"]

    base = "RETRIES = 3\n\ndef f(s):\n    '''Doc.'''\n    return s.get('u', timeout=30, tries=RETRIES)\n"
    assert bfp(base) == bfp(base.replace("'''Doc.'''", "'''Other doc.'''").replace("return", "# why\n    return"))
    assert bfp(base) != bfp(base.replace("timeout=30", "timeout=5"))
    assert bfp(base) != bfp(base.replace("RETRIES = 3", "RETRIES = 1"))
    sfp = next(f for f in extract_python(base, "m.py").functions)["sfp"]
    assert sfp == next(f for f in extract_python(base.replace("timeout=30", "timeout=5"), "m.py").functions)["sfp"]


def test_non_python_callables_get_a_fingerprint_that_moves_with_the_body():
    from srlib.facts import extract_source

    ts = "export function hello(name: string): string {\n  // greet\n  return `hi ${name}`;\n}\n"
    one = extract_source(ts, "web.ts").functions[0]
    assert one["sfp"]
    assert extract_source(ts.replace("// greet", "// say hi"), "web.ts").functions[0]["sfp"] == one["sfp"]
    assert extract_source(ts.replace("hi ", "hello "), "web.ts").functions[0]["sfp"] != one["sfp"]


def test_hazards_calls_on_io_client_objects_are_external():
    src = (
        "import sqlite3, asyncpg\n"
        "class R:\n"
        "    def __init__(self):\n        self.db = sqlite3.connect('x.db')\n"
        "    def save(self, v):\n        self.db.execute('INSERT INTO t VALUES (?)', (v,))\n"
        "async def fetch():\n    pool = await asyncpg.create_pool()\n    return await pool.fetch('select 1')\n"
    )
    assert ("external", "self.db.execute") in _kinds(src, "save")
    assert ("external", "pool.fetch") in _kinds(src, "fetch")


def test_hazards_sql_writes_in_fstrings_module_constants_and_ddl():
    src = 'DEL = "DELETE FROM jobs WHERE id = ?"\ndef f(c, t):\n    c.execute(DEL)\n    c.execute(f"TRUNCATE {t}")\n'
    assert {("write", "sql:DELETE jobs"), ("write", "sql:TRUNCATE ?")} <= _kinds(src, "f")


def test_hazards_file_writes_through_methods_and_os_but_not_stdout():
    src = (
        "import os, shutil, sys\n"
        "def f(p, d):\n"
        "    p.write_text('x')\n    os.remove(d)\n    shutil.rmtree(d)\n"
        "    with open(p, 'w') as fh:\n        fh.write('y')\n"
        "    sys.stdout.write('z')\n"
    )
    writes = {what for kind, what in _kinds(src, "f") if kind == "write"}
    assert {"p.write_text", "os.remove", "shutil.rmtree", "fh.write"} <= writes
    assert "sys.stdout.write" not in writes


def test_lock_detection_matches_lock_names_not_substrings():
    src = (
        "import threading\n"
        "def f(self):\n"
        "    with self.clock:\n        pass\n"
        "    with blocking_io():\n        pass\n"
        "    with self._lock:\n        pass\n"
        "    with self.state_rlock:\n        pass\n"
        "    with threading.Semaphore(2):\n        pass\n"
    )
    locks = {what for kind, what in _kinds(src) if kind == "lock"}
    assert locks == {"self._lock", "self.state_rlock", "threading.Semaphore(2)"}


def test_acquire_is_a_lock_only_on_a_lock_named_receiver():
    src = "def f(self, pool):\n    self._mutex.acquire()\n    pool.acquire()\n"
    assert {what for kind, what in _kinds(src) if kind == "lock"} == {"self._mutex"}


def test_http_status_import_is_not_external():
    src = ("from http import HTTPStatus\nfrom http.client import HTTPConnection\n"
           "def f():\n    HTTPStatus(200)\n    HTTPConnection('h')\n")
    assert {what for kind, what in _kinds(src) if kind == "external"} == {"HTTPConnection"}


def test_spawn_needs_a_concurrency_source():
    src = (
        "import threading, psutil\nfrom concurrent.futures import ThreadPoolExecutor\n"
        "def f(form, ex: ThreadPoolExecutor):\n"
        "    psutil.Process(1)\n    form.submit()\n"
        "    threading.Thread(target=g).start()\n"
        "    with ThreadPoolExecutor() as executor:\n        executor.submit(g)\n"
    )
    assert {what for kind, what in _kinds(src) if kind == "spawn"} == {"threading.Thread", "executor.submit"}


def test_building_an_exception_is_not_an_external_call():
    src = "import requests\ndef f(r):\n    raise requests.HTTPError('bad')\n"
    assert not any(kind == "external" for kind, _ in _kinds(src))


def test_locks_found_through_aliases_subscripts_and_lock_calls():
    src = (
        "import threading, asyncio\nfrom filelock import FileLock\n"
        "guard = threading.Lock()\n"
        "class S:\n"
        "    def __init__(self):\n        self._guard = asyncio.Lock()\n        self._locks = {}\n"
        "    async def f(self, k, r):\n"
        "        guard.acquire()\n"
        "        with guard:\n            pass\n"
        "        async with self._guard:\n            pass\n"
        "        with self._locks[k]:\n            pass\n"
        "        with FileLock('x.lock'):\n            pass\n"
        "        with self._lock_for(k):\n            pass\n"
        "        with r.lock('name'):\n            pass\n"
    )
    locks = {what for kind, what in _kinds(src, "f") if kind == "lock"}
    assert {"guard", "self._guard", "self._locks[k]", "FileLock('x.lock')", "self._lock_for(k)",
            "r.lock('name')"} <= locks


def test_spawns_found_on_executor_receivers_and_mp_contexts():
    src = (
        "import multiprocessing\nfrom app.pools import EXECUTOR\n"
        "def f(executor):\n"
        "    executor.submit(g)\n    EXECUTOR.submit(g)\n"
        "    ctx = multiprocessing.get_context('spawn')\n    ctx.Process(target=g).start()\n"
    )
    spawns = {what for kind, what in _kinds(src) if kind == "spawn"}
    assert {"executor.submit", "EXECUTOR.submit", "ctx.Process"} <= spawns


def test_calls_inside_a_raise_still_count():
    src = "import requests\ndef f(url):\n    raise RuntimeError(requests.get(url).text)\n"
    assert ("external", "requests.get") in _kinds(src)


def test_optional_import_is_not_a_swallow():
    src = "try:\n    import yaml\n    HAVE_YAML = True\nexcept ImportError:\n    yaml = None\n    HAVE_YAML = False\n"
    facts = extract_python(src, "m.py")
    assert not any(h["kind"] == "swallow" for h in facts.module_hazards)


def test_same_name_for_a_spawn_pool_and_an_io_pool_keeps_both_meanings():
    src = (
        "import multiprocessing, asyncpg\n"
        "def a():\n    pool = multiprocessing.Pool()\n"
        "async def b():\n    pool = await asyncpg.create_pool()\n    await pool.fetch('select 1')\n"
    )
    assert any(kind == "external" and what.startswith("pool.fetch") for kind, what in _kinds(src, "b"))


def test_mutating_an_argument_is_an_arg_write():
    src = ("def check(repos, last_revive, history, now):\n"
           "    last_revive[repos[0]] = now\n    history.append(now)\n    seen = {}\n    seen['x'] = 1\n")
    found = _kinds(src)
    assert {("arg_write", "last_revive"), ("arg_write", "history")} <= found
    assert not any(what in ("repos", "seen") for kind, what in found if kind == "arg_write")
