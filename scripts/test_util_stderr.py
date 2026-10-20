"""Native-library noise on stderr is dropped; everything else on stderr survives."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from srlib.util import quiet_native_noise


def test_fontconfig_warnings_are_dropped_and_other_stderr_is_kept(capfd):
    with quiet_native_noise():
        os.write(2, b'Fontconfig warning: "/x.conf", line 6: unknown element "reset-dirs"\n')
        os.write(2, b"real problem\n")
    err = capfd.readouterr().err
    assert "Fontconfig" not in err and "real problem" in err
