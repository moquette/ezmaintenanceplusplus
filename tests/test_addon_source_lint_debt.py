"""GAP 6: tracked, deliberate ruff debt in script.ezmaintenanceplusplus/ - not fixed here.

WHY THIS EXISTS
---------------
CI intentionally scopes `ruff check` to tests/ + tools/ (see .github/workflows/ci.yml's
"Lint (tests + tooling)" step) and does not lint the add-on source itself. That source
carries 41 pre-existing findings (bare `except:`, etc.) inherited from the upstream EZ
Maintenance+ fork, predating this repo's test suite entirely.

Mass-fixing those findings - e.g. turning a bare `except:` into `except Exception:`, or
letting ruff's `--fix`/`--unsafe-fixes` loose on the module - changes RUNTIME BEHAVIOR in
code that ships to a live fleet, unreviewed. That is exactly the "fixed in code, not
proven" mistake this project's house rules exist to prevent (CLAUDE.md: "implement ->
TEST -> gate -> adversarial QA -> REAL-DEVICE verify -> document -> only then
commit/release. No 'fixed in code' claims without hardware proof."). A bare `except:`
silently swallowing a different exception than the author intended, once narrowed, can
change what a backup/restore path does on a real box - not something to do as a lint
drive-by.

WHAT THIS TEST IS (AND ISN'T)
------------------------------
This is a TRACKED, VISIBLE marker for that debt - not a silent skip, and not a lint
gate. It is `xfail(strict=False)`:

  - As long as script.ezmaintenanceplusplus/ has ruff findings, this reports as an
    expected failure (XFAIL) - visible in every pytest summary, never reds CI.
  - If someone genuinely cleans the source (reviewed, tested, device-verified per the
    house rules above) and it goes ruff-clean, this reports XPASS - also non-fatal
    (strict=False), and is the signal to delete this test rather than the debt quietly
    vanishing unnoticed.

Re-run `ruff check script.ezmaintenanceplusplus/` locally to see the current findings.
"""

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADDON = ROOT / "script.ezmaintenanceplusplus"


@pytest.mark.xfail(
    reason=(
        "pre-existing ruff findings inherited from the upstream EZ Maintenance+ fork "
        "(bare excepts, etc.) - tracked debt, deliberately not blind-fixed (see this "
        "test's module docstring); not a regression gate"
    ),
    strict=False,
)
def test_addon_source_is_ruff_clean():
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff not installed")
    result = subprocess.run(
        [ruff, "check", str(ADDON)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        "script.ezmaintenanceplusplus/ has ruff findings (tracked, deliberate debt - "
        "see this test's module docstring for why it is not blind-fixed):\n"
        + result.stdout
    )
