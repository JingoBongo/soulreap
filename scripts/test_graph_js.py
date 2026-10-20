"""JS/TS import resolution through tsconfig/jsconfig paths and baseUrl."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.build import build_facts


def _targets(tmp_path: Path, files: dict[str, str]) -> dict[str, dict]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    facts = build_facts(tmp_path).files
    return {p: {i["module"]: i["target"] for i in b["imports"]} for p, b in facts.items()}


def test_paths_alias_resolves_relative_to_the_config_directory(tmp_path):
    files = {
        "frontend/tsconfig.json": (
            "{\n // comment\n \"compilerOptions\": {\"baseUrl\": \".\", \"paths\": {\"@/*\": [\"./src/*\"],},},\n}"),
        "frontend/src/lib/util.ts": "export const u = 1;\n",
        "frontend/src/components/Btn.tsx": "export const Btn = () => null;\n",
        "frontend/src/app/page.tsx": "import { u } from '@/lib/util';\nimport { Btn } from '@/components/Btn';\n",
        "src/lib/util.ts": "export const decoy = 1;\n",
    }
    got = _targets(tmp_path, files)["frontend/src/app/page.tsx"]
    assert got == {"@/lib/util": "frontend/src/lib/util.ts", "@/components/Btn": "frontend/src/components/Btn.tsx"}


def test_custom_alias_and_exact_pattern_and_index(tmp_path):
    files = {
        "tsconfig.json": '{"compilerOptions": {"paths": {"~app/*": ["web/*"], "config": ["cfg/index.ts"]}}}',
        "web/ui/index.ts": "export {};\n",
        "cfg/index.ts": "export {};\n",
        "web/main.ts": "import a from '~app/ui';\nimport c from 'config';\nimport r from 'react';\n",
    }
    got = _targets(tmp_path, files)["web/main.ts"]
    assert got == {"~app/ui": "web/ui/index.ts", "config": "cfg/index.ts", "react": None}


def test_base_url_resolves_bare_specifiers(tmp_path):
    files = {
        "tsconfig.json": '{"compilerOptions": {"baseUrl": "src"}}',
        "src/utils/a.ts": "export {};\n",
        "src/main.ts": "import a from 'utils/a';\n",
    }
    assert _targets(tmp_path, files)["src/main.ts"] == {"utils/a": "src/utils/a.ts"}


def test_extends_one_level_relative(tmp_path):
    files = {
        "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@x/*": ["packages/x/*"]}}}',
        "app/tsconfig.json": '{"extends": "../tsconfig.base.json"}',
        "packages/x/m.ts": "export {};\n",
        "app/a.ts": "import m from '@x/m';\n",
    }
    assert _targets(tmp_path, files)["app/a.ts"] == {"@x/m": "packages/x/m.ts"}


def test_nearest_config_wins(tmp_path):
    files = {
        "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./nope/*"]}}}',
        "sub/tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./lib/*"]}}}',
        "sub/lib/z.ts": "export {};\n",
        "sub/a.ts": "import z from '@/z';\n",
    }
    assert _targets(tmp_path, files)["sub/a.ts"] == {"@/z": "sub/lib/z.ts"}


def test_without_a_config_the_old_guesses_still_work(tmp_path):
    files = {
        "src/lib/z.ts": "export {};\n",
        "src/a.ts": "import z from '@/lib/z';\nimport y from './missing';\n",
    }
    assert _targets(tmp_path, files)["src/a.ts"] == {"@/lib/z": "src/lib/z.ts", "./missing": None}


def test_js_extension_in_an_esm_specifier_finds_the_ts_file(tmp_path):
    files = {"src/b.ts": "export {};\n", "src/a.ts": "import b from './b.js';\n"}
    assert _targets(tmp_path, files)["src/a.ts"] == {"./b.js": "src/b.ts"}
