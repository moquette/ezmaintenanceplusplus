"""The unreleased-changes gate: source that moved at an already-released version.

Exercises tools/check_unreleased_changes.classify(), which is pure precisely so
this suite never shells out to git or touches the network. Every scenario below
is one of the three states the gate can be in.

The hole being closed (2026-07-25 pipeline audit): the publish job is idempotent
by tag, so a source change committed WITHOUT bumping addon.xml builds, tests
green, publishes nothing, reaches zero boxes, and nothing goes red anywhere.

The deliberately-permitted case is test_dirty_is_a_warning_not_a_failure_by_default:
batching several commits into one later release is the normal workflow here
(7b34e76, c5f3a44 and e52d170 were all batched into "Release 2026.07.22.0"), so
the default must not fail. A gate that fought the real workflow would be
bypassed, which is how the office hands-off rule and the two-agent review died.
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "check_unreleased_changes.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_unreleased_changes", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


def test_unreleased_version_is_clean(mod):
    """A version with no tag yet is the normal pre-release state, not a problem."""
    state, message = mod.classify("2026.07.22.4", has_tag=False, changed=[])
    assert state == "unreleased"
    assert "no v2026.07.22.4 tag yet" in message


def test_released_and_unchanged_is_clean(mod):
    """The genuine idempotent no-op: tag exists, nothing moved since."""
    state, message = mod.classify("2026.07.22.3", has_tag=True, changed=[])
    assert state == "clean"
    assert "unchanged" in message


def test_source_changed_at_released_version_is_dirty(mod):
    """The actual failure the audit found, and the whole reason this gate exists."""
    state, message = mod.classify(
        "2026.07.21.5",
        has_tag=True,
        changed=["script.ezmaintenanceplusplus/default.py"],
    )
    assert state == "dirty"
    assert "reach NO box" in message
    assert "script.ezmaintenanceplusplus/default.py" in message


def test_dirty_is_a_warning_not_a_failure_by_default(mod):
    """Batching is the real workflow, so the default must not block a push."""
    assert mod.main([]) in (0,), "default mode must never fail a push"


def test_dirty_message_lists_files_but_truncates(mod):
    """A 200-file diff must not bury the message it is attached to."""
    changed = [f"script.ezmaintenanceplusplus/f{i}.py" for i in range(25)]
    _, message = mod.classify("1.0.0", has_tag=True, changed=changed)
    assert "... and 5 more" in message
    assert message.count("script.ezmaintenanceplusplus/f") == 20


def test_version_is_read_off_the_addon_element(mod, tmp_path):
    """Must read the <addon> element, not the first version= in the file."""
    xml = tmp_path / "addon.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<addon id="script.ezmaintenanceplusplus" version="2026.07.22.3">\n'
        '  <requires><import addon="xbmc.python" version="3.0.0"/></requires>\n'
        "</addon>\n",
        encoding="utf-8",
    )
    assert mod.read_version(str(xml)) == "2026.07.22.3"


def test_real_addon_xml_parses(mod):
    """The gate is worthless if it cannot read this repo's own addon.xml."""
    version = mod.read_version()
    assert version, "no version parsed from the live addon.xml"
    assert version[0].isdigit()
