# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
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
import os
import subprocess
from pathlib import Path

import pytest

# pylint: disable=redefined-outer-name

SCRIPT = (
    Path(__file__).parents[2] / "dom0-updates" / "qubes-check-reboot-required"
)


def boot_time() -> int:
    with open("/proc/stat", encoding="ascii") as stat:
        for line in stat:
            if line.startswith("btime "):
                return int(line.split()[1])
    raise AssertionError("no btime in /proc/stat")


@pytest.fixture
def run_script(tmp_path):
    """
    Run the script with fake `rpm` and `qvm-features` commands.

    `rpm` prints the given (install time, name) pairs and `qvm-features`
    records its arguments.
    """

    def run(packages):
        rpm_output = "".join(f"{time} {name}\n" for time, name in packages)
        (tmp_path / "rpm.out").write_text(rpm_output)
        for cmd, body in (
            ("rpm", f'cat "{tmp_path}/rpm.out"'),
            ("qvm-features", f'echo "$*" >> "{tmp_path}/features.log"'),
        ):
            path = tmp_path / cmd
            path.write_text(f"#!/bin/sh\n{body}\n")
            path.chmod(0o755)
        env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        features = (tmp_path / "features.log").read_text().splitlines()
        return result.stdout, features

    return run


def test_reboot_required(run_script):
    boot = boot_time()
    stdout, features = run_script(
        [
            (boot - 100, "glibc"),
            (boot + 100, "xen-hypervisor"),
            (boot + 200, "kernel"),
            (boot + 300, "kernel"),
        ]
    )
    assert stdout == "kernel xen-hypervisor\n"
    assert features == ["dom0 reboot-required kernel xen-hypervisor"]


def test_no_reboot_required(run_script):
    boot = boot_time()
    stdout, features = run_script([(boot - 100, "kernel"), (boot, "glibc")])
    assert stdout == ""
    assert features == ["--unset dom0 reboot-required"]
