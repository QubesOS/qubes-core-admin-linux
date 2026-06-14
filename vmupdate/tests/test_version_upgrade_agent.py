#!/usr/bin/python3
# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2026  Qubes OS contributors
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
"""
Unit tests for the in-VM distribution version-upgrade agent path.

The agent modules use absolute ``source.*`` imports because inside a qube
they run with the agent directory as the top-level package root (see
``entrypoint.py``). We mirror that here by putting the agent directory on
``sys.path`` so the agent can be imported and unit-tested in isolation, with
``subprocess`` fully mocked -- no real dnf is ever invoked.
"""

import os
import sys
import logging
import argparse

from unittest.mock import patch, MagicMock

import pytest

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "agent")
)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

# pylint: disable=wrong-import-position
import entrypoint
from source.dnf.dnf_cli import DNFCLI
from source.common.package_manager import PackageManager, AgentType
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT
from source.args import AgentArgs


def _expected_sync_cmd(target):
    return (
        "dnf",
        f"--releasever={target}",
        "distro-sync",
        "--best",
        "--allowerasing",
        "--assumeyes",
    )


def make_dnf_cli():
    """Build a DNFCLI without requiring a real dnf binary on the host."""
    with patch("source.dnf.dnf_cli.shutil.which", return_value="/usr/bin/dnf"):
        return DNFCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)


def fedora_os_data(release="41"):
    return {"id": "fedora", "os_family": "RedHat", "release": release}


# DNFCLI._release_upgrade -- happy path


def test_version_upgrade_runs_clean_then_distro_sync():
    mgr = make_dnf_cli()
    calls = []

    def fake_run_cmd(cmd, realtime=True):
        calls.append((tuple(cmd), realtime))
        return ProcessResult(EXIT.OK)

    with patch.object(mgr, "run_cmd", side_effect=fake_run_cmd), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK
    # Old-release cache is wiped (captured, not streamed) before the bump.
    assert calls[0] == (("dnf", "clean", "all"), False)
    sync_cmd, sync_realtime = calls[1]
    assert sync_cmd == _expected_sync_cmd("42")
    # distro-sync streams in real time so dom0 sees live output.
    assert sync_realtime is True


def test_version_upgrade_emits_progress_milestones(capsys):
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        mgr.version_upgrade("42")

    # The progress contract QubeConnection._collect_stderr parses: bare floats
    # terminated by 100.00.
    assert capsys.readouterr().err.split() == ["0.00", "100.00"]


# DNFCLI._release_upgrade -- in-qube re-verification (single-step only)


def test_version_upgrade_refuses_non_numeric_target():
    mgr = make_dnf_cli()
    with patch.object(mgr, "run_cmd") as run_cmd, patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("bookworm")

    assert code == EXIT.ERR_VM_UPDATE
    run_cmd.assert_not_called()


@pytest.mark.parametrize(
    "target,current",
    [
        ("41", "41"),  # no-op
        ("40", "41"),  # downgrade
        ("43", "41"),  # two-step jump
    ],
)
def test_version_upgrade_enforces_single_step(target, current):
    mgr = make_dnf_cli()
    with patch.object(mgr, "run_cmd") as run_cmd, patch(
        "source.dnf.dnf_cli.get_os_data",
        return_value=fedora_os_data(current),
    ):
        code = mgr.version_upgrade(target)

    assert code == EXIT.ERR_VM_UPDATE
    run_cmd.assert_not_called()


def test_version_upgrade_allows_dotted_current_release():
    # VERSION_ID like "41.20240101" should compare on the major component.
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ) as run_cmd, patch(
        "source.dnf.dnf_cli.get_os_data",
        return_value=fedora_os_data("41.20240101"),
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK
    assert run_cmd.call_count == 2


# DNFCLI._release_upgrade -- failure mapping


def test_version_upgrade_bails_when_clean_fails():
    mgr = make_dnf_cli()
    calls = []

    def fake_run_cmd(cmd, realtime=True):
        calls.append(tuple(cmd))
        return ProcessResult(3)  # clean all fails

    with patch.object(mgr, "run_cmd", side_effect=fake_run_cmd), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.ERR_VM_UPDATE
    # distro-sync is never attempted once the cache wipe fails.
    assert calls == [("dnf", "clean", "all")]


def test_version_upgrade_maps_distro_sync_failure():
    mgr = make_dnf_cli()

    def fake_run_cmd(cmd, realtime=True):
        if "distro-sync" in cmd:
            return ProcessResult(7)  # arbitrary non-zero dnf failure
        return ProcessResult(EXIT.OK)

    with patch.object(mgr, "run_cmd", side_effect=fake_run_cmd), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    # Any non-zero is normalised to a dom0-handled VM error code.
    assert code == EXIT.ERR_VM_UPDATE


# Base class -- loud fail-closed default, covers Debian/apt + Arch


def test_base_version_upgrade_fails_loud():
    # Families without a real implementation must raise NotImplementedError
    # so callers can decide how to log and map the unsupported path.
    mgr = PackageManager(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    with pytest.raises(NotImplementedError, match="not implemented"):
        mgr._release_upgrade("42")
    with pytest.raises(NotImplementedError, match="not implemented"):
        mgr.version_upgrade("42")


# Agent CLI surface -- args round-trip


def _parse_agent_args(argv):
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)
    return parser.parse_args(argv)


def test_version_upgrade_flag_round_trips_through_cli_args():
    args = _parse_agent_args(["--version-upgrade", "42"])
    assert args.version_upgrade == "42"

    cli = AgentArgs.to_cli_args(args)
    assert "--version-upgrade" in cli
    assert cli[cli.index("--version-upgrade") + 1] == "42"


def test_version_upgrade_flag_absent_by_default():
    args = _parse_agent_args([])
    assert args.version_upgrade is None

    cli = AgentArgs.to_cli_args(args)
    assert "--version-upgrade" not in cli
    # Regression guard: a None-valued option must never leak a bare token.
    assert None not in cli


# Entrypoint dispatch


def _patched_entrypoint(pkg_mng):
    """Common patches so entrypoint.main runs without a real qube/logs."""
    fake_logs = (MagicMock(), MagicMock(), logging.DEBUG, "", "")
    return (
        patch("entrypoint.init_logs", return_value=fake_logs),
        patch("entrypoint.get_os_data", return_value=fedora_os_data("41")),
        patch("entrypoint.get_package_manager", return_value=pkg_mng),
        patch("entrypoint.os.system"),
    )


def test_entrypoint_dispatches_to_version_upgrade():
    pkg_mng = MagicMock()
    pkg_mng.version_upgrade.return_value = EXIT.OK
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main(["--version-upgrade", "42"])

    pkg_mng.version_upgrade.assert_called_once_with("42", print_streams=False)
    pkg_mng.upgrade.assert_not_called()
    assert code == EXIT.OK


def test_entrypoint_maps_missing_version_upgrade_to_handled_error(capsys):
    pkg_mng = MagicMock()
    pkg_mng.version_upgrade.side_effect = NotImplementedError(
        "Distribution version upgrade is not implemented for this package manager."
    )
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main(["--version-upgrade", "42"])

    pkg_mng.version_upgrade.assert_called_once_with("42", print_streams=False)
    pkg_mng.upgrade.assert_not_called()
    assert code == EXIT.ERR_VM_UPDATE
    assert "not implemented" in capsys.readouterr().err


def test_entrypoint_runs_normal_update_without_flag():
    pkg_mng = MagicMock()
    pkg_mng.upgrade.return_value = EXIT.OK
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main([])

    pkg_mng.upgrade.assert_called_once()
    pkg_mng.version_upgrade.assert_not_called()
    assert code == EXIT.OK
