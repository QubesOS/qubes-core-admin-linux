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
``subprocess`` fully mocked -- no real package manager is ever invoked.
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
from source.apt.apt_cli import APTCLI
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
        ("41", "41"),
        ("40", "41"),
        ("43", "41"),
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


# Base class -- fail-closed default for families without an implementation


def test_base_version_upgrade_fails_loud():
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


# APTCLI Debian release-upgrade path


def test_apt_api_refresh_reloads_sources_before_update():
    apt_api = pytest.importorskip("source.apt.apt_api")
    mgr = apt_api.APT.__new__(apt_api.APT)
    mgr.wait_for_lock = MagicMock()
    mgr.apt_cache = MagicMock()
    mgr.progress = MagicMock()
    mgr.log = MagicMock()
    calls = []

    mgr.apt_cache.open.side_effect = lambda: calls.append("open")

    def update(*_args, **_kwargs):
        calls.append("update")
        return True

    mgr.apt_cache.update.side_effect = update

    result = mgr.refresh(hard_fail=True)

    assert result.code == EXIT.OK
    assert calls == ["open", "update", "open"]


def make_apt_cli():
    """Build an APTCLI without requiring a real apt on the host."""
    return APTCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)


def debian_os_data(release="12", codename="bookworm"):
    return {
        "id": "debian",
        "os_family": "Debian",
        "release": release,
        "codename": codename,
    }


# APTCLI in-qube guard (single-step, Debian-only, codenames must resolve)


def _apt_guard(os_data, target):
    return make_apt_cli()._verify_release_upgrade(target, os_data)


def test_apt_guard_refuses_non_debian_family():
    assert _apt_guard(fedora_os_data("41"), "42").code == EXIT.ERR_VM_UPDATE


def test_apt_guard_refuses_non_numeric_target():
    result = _apt_guard(debian_os_data("12", "bookworm"), "trixie")
    assert result.code == EXIT.ERR_VM_UPDATE


@pytest.mark.parametrize(
    "target,current",
    [
        ("12", "12"),
        ("11", "12"),
        ("14", "12"),
    ],
)
def test_apt_guard_enforces_single_step(target, current):
    result = _apt_guard(debian_os_data(current, "bookworm"), target)
    assert result.code == EXIT.ERR_VM_UPDATE


def test_apt_guard_refuses_unknown_target_codename():
    # 14->15 is a single step, but 15 is not in DEBIAN_CODENAMES yet
    result = _apt_guard(debian_os_data("14", "forky"), "15")
    assert result.code == EXIT.ERR_VM_UPDATE


def test_apt_guard_refuses_missing_codename():
    os_data = {"id": "debian", "os_family": "Debian", "release": "12"}
    assert _apt_guard(os_data, "13").code == EXIT.ERR_VM_UPDATE


def test_apt_guard_passes_for_single_step_debian():
    assert _apt_guard(debian_os_data("12", "bookworm"), "13").code == EXIT.OK


# APTCLI._release_upgrade composition (apt mocked at the step level)


def _record_apt_steps(mgr, calls, fail_on=None, cleanup_fail=False):
    """Replace the composed steps with recorders."""

    def refresh(hard_fail):
        calls.append("refresh")
        return ProcessResult(EXIT.ERR if fail_on == "refresh" else EXIT.OK)

    def upgrade_internal(remove_obsolete):
        calls.append("upgrade")
        return ProcessResult(EXIT.ERR if fail_on == "upgrade" else EXIT.OK)

    def dist_upgrade():
        calls.append("dist-upgrade")
        return ProcessResult(EXIT.ERR if fail_on == "dist-upgrade" else EXIT.OK)

    def rewrite(old_codename, new_codename):
        calls.append(f"rewrite:{old_codename}->{new_codename}")
        return ProcessResult(EXIT.ERR if fail_on == "rewrite" else EXIT.OK)

    def remove_obsolete_kernels():
        calls.append("kernel-cleanup")
        return ProcessResult(EXIT.ERR_VM_CLEANUP if cleanup_fail else EXIT.OK)

    mgr.refresh = refresh
    mgr.upgrade_internal = upgrade_internal
    mgr._dist_upgrade = dist_upgrade
    mgr._rewrite_sources = rewrite
    mgr.remove_obsolete_kernels = remove_obsolete_kernels


def test_apt_release_upgrade_happy_path_order(capsys):
    mgr = make_apt_cli()
    calls = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[
            debian_os_data("12", "bookworm"),
            debian_os_data("13", "trixie"),
        ],
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.OK
    assert calls == [
        "refresh",
        "upgrade",
        "rewrite:bookworm->trixie",
        "refresh",
        "upgrade",
        "dist-upgrade",
        "kernel-cleanup",
    ]
    # the QubeConnection progress contract: bare floats, terminated by 100.00
    assert capsys.readouterr().err.split() == ["0.00", "100.00"]


def test_apt_release_upgrade_survives_kernel_cleanup_failure(capsys):
    # a successful release bump must not be discarded because the trailing
    # best-effort obsolete-kernel cleanup failed
    mgr = make_apt_cli()
    calls = []
    _record_apt_steps(mgr, calls, cleanup_fail=True)
    with patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[
            debian_os_data("12", "bookworm"),
            debian_os_data("13", "trixie"),
        ],
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.OK
    assert calls[-1] == "kernel-cleanup"
    assert capsys.readouterr().err.split() == ["0.00", "100.00"]


def test_apt_release_upgrade_bails_before_rewrite_when_update_fails(capsys):
    mgr = make_apt_cli()
    calls = []
    _record_apt_steps(mgr, calls, fail_on="refresh")
    with patch(
        "source.apt.apt_cli.get_os_data",
        return_value=debian_os_data("12", "bookworm"),
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.ERR_VM_UPDATE
    # first apt-get update fails: sources never rewritten, no dist-upgrade
    assert calls == ["refresh"]
    assert capsys.readouterr().err.split() == ["0.00"]


def test_apt_release_upgrade_maps_dist_upgrade_failure(capsys):
    mgr = make_apt_cli()
    calls = []
    _record_apt_steps(mgr, calls, fail_on="dist-upgrade")
    with patch(
        "source.apt.apt_cli.get_os_data",
        return_value=debian_os_data("12", "bookworm"),
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.ERR_VM_UPDATE
    assert calls[-1] == "dist-upgrade"
    assert "100.00" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "actual",
    [
        debian_os_data("12", "bookworm"),
        debian_os_data("13", "bookworm"),
    ],
)
def test_apt_release_upgrade_verifies_target_release(actual, capsys):
    mgr = make_apt_cli()
    calls = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[debian_os_data("12", "bookworm"), actual],
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.ERR_VM_UPDATE
    assert calls[-1] == "dist-upgrade"
    assert "kernel-cleanup" not in calls
    assert "100.00" not in capsys.readouterr().err


def test_apt_release_upgrade_guard_failure_short_circuits(capsys):
    mgr = make_apt_cli()
    calls = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.ERR_VM_UPDATE
    assert calls == []  # no apt call, no rewrite, no progress
    assert capsys.readouterr().err.split() == []


# apt sources codename rewrite


def test_apt_rewrites_list_and_deb822_sources(tmp_path):
    mgr = make_apt_cli()
    listd = tmp_path / "sources.list.d"
    listd.mkdir()
    main = tmp_path / "sources.list"
    main.write_text("deb http://deb.debian.org/debian bookworm main\n")
    qubes = listd / "qubes-r4.list"
    qubes.write_text(
        "deb [arch=amd64] https://deb.qubes-os.org/r4.2/vm bookworm main\n"
    )
    deb822 = listd / "debian.sources"
    deb822.write_text(
        "Types: deb\n"
        "URIs: http://deb.debian.org/debian\n"
        "Suites: bookworm bookworm-security bookworm-updates\n"
        "Components: main\n"
    )
    untouched = listd / "thirdparty.list"
    untouched.write_text("deb http://example.com/repo stable main\n")
    before = untouched.read_text()

    globs = (
        str(main),
        str(tmp_path / "absent.list"),  # missing files are skipped
        str(listd / "*.list"),
        str(listd / "*.sources"),
    )
    with patch.object(APTCLI, "APT_SOURCE_GLOBS", globs):
        result = mgr._rewrite_sources("bookworm", "trixie")

    assert result.code == EXIT.OK
    assert main.read_text() == "deb http://deb.debian.org/debian trixie main\n"
    assert "trixie" in qubes.read_text() and "bookworm" not in qubes.read_text()
    assert "Suites: trixie trixie-security trixie-updates" in deb822.read_text()
    # a file with no codename occurrence is left byte-identical
    assert untouched.read_text() == before
    # the atomic write-then-rename leaves no temp files behind
    assert list(tmp_path.rglob("*.tmp")) == []


def test_apt_rewrite_refuses_when_no_source_uses_the_codename(tmp_path):
    # symbolically addressed sources (e.g. `stable`) never mention the
    # codename: the rewrite would be a no-op, so refuse rather than let the
    # qube silently stay on the old release while dom0 stamps it upgraded
    mgr = make_apt_cli()
    listd = tmp_path / "sources.list.d"
    listd.mkdir()
    main = tmp_path / "sources.list"
    main.write_text("deb http://deb.debian.org/debian stable main\n")
    before = main.read_text()
    globs = (str(main), str(listd / "*.list"), str(listd / "*.sources"))
    with patch.object(APTCLI, "APT_SOURCE_GLOBS", globs):
        result = mgr._rewrite_sources("bookworm", "trixie")

    assert result.code == EXIT.ERR_VM_UPDATE
    assert main.read_text() == before  # nothing written
