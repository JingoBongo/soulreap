"""Path classification: what counts as a test file."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_django_tests_module_is_a_test_path():
    from srlib.paths import is_test_path

    assert is_test_path("apps/authentication/tests.py") and not is_test_path("apps/authentication/views.py")
