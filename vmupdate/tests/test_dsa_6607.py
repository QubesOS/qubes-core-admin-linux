# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.
import importlib.util
import logging
import os

import pytest

PLUGIN = os.path.join(
    os.path.dirname(__file__),
    "..",
    "agent",
    "source",
    "plugins",
    "dsa_6607.py",
)

log = logging.getLogger("test")

STUBS = {
    "dpkg": """\
if [ "$1" = "-s" ]; then
    echo "Version: $(cat "$STATE/version")"
elif [ "$1" = "--compare-versions" ]; then
    [ "$(printf '%s\\n%s\\n' "$2" "$4" | sort -V | head -n1)" = "$4" ]
fi
""",
    "apt-get": """\
echo "$*" >> "$STATE/calls"
for arg; do last="$arg"; done
fail="$STATE/fail-$last"
if [ -f "$fail" ]; then
    mv "$fail" "$fail.done"
    exit 100
fi
if [ "$last" = "apt" ]; then
    echo 3.0.4 > "$STATE/version"
fi
""",
    "rm": 'echo "$*" >> "$STATE/rm"\n',
    "sleep": "true\n",
}


@pytest.fixture(name="dsa_6607")
def fixture_dsa_6607():
    spec = importlib.util.spec_from_file_location("dsa_6607", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.dsa_6607


@pytest.fixture(name="state")
def fixture_state(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in STUBS.items():
        stub = bindir / name
        stub.write_text("#!/bin/bash\n" + body)
        stub.chmod(0o755)
    statedir = tmp_path / "state"
    statedir.mkdir()
    (statedir / "version").write_text("3.0.3\n")
    monkeypatch.setenv("STATE", str(statedir))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return statedir


def test_already_fixed(dsa_6607, state):
    """No apt call when the installed apt is already fixed."""
    (state / "version").write_text("3.0.4\n")
    dsa_6607({"os_family": "Debian"}, log)
    assert not (state / "calls").exists()


def test_update_fetch_error_once(dsa_6607, state):
    """One failed index fetch does not fail the plugin."""
    (state / "fail-update").touch()
    dsa_6607({"os_family": "Debian"}, log)
    assert (state / "version").read_text() == "3.0.4\n"


def test_install_fetch_error_once(dsa_6607, state):
    """One failed package fetch is retried in a fresh apt run."""
    (state / "fail-apt").touch()
    dsa_6607({"os_family": "Debian"}, log)
    calls = (state / "calls").read_text().splitlines()
    assert len(calls) == 4
    assert all(c.startswith("-o Acquire::Retries=0 ") for c in calls)
    assert len((state / "rm").read_text().splitlines()) == 2


def test_never_fixed(dsa_6607, state):
    """The plugin still fails when apt stays unfixed."""
    apt_get = state.parent / "bin" / "apt-get"
    apt_get.write_text("#!/bin/bash\nexit 100\n")
    with pytest.raises(IOError):
        dsa_6607({"os_family": "Debian"}, log)
