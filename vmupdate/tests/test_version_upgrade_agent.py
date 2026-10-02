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

from types import SimpleNamespace
from unittest.mock import patch, MagicMock, Mock, mock_open

import pytest

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "agent")
)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

# pylint: disable=wrong-import-position,protected-access
import entrypoint
from source.dnf.dnf_cli import DNFCLI
from source.apt.apt_cli import APTCLI
from source.common.package_manager import PackageManager, AgentType
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT
from source.common.package_manager import RELEASE_UPGRADE_ALMOST_DONE
from source.common.progress_reporter import (
    Progress,
    ProgressReporter,
    ReleaseUpgradeTail,
)
from source.args import AgentArgs


def _expected_sync_cmd(package_manager: str, target: str) -> tuple[str, ...]:
    return (
        package_manager,
        f"--releasever={target}",
        "distro-sync",
        "--best",
        "--allowerasing",
        "--assumeyes",
    )


def make_dnf_cli() -> DNFCLI:
    """Build a DNFCLI without requiring a real dnf binary on the host."""
    with patch("source.dnf.dnf_cli.shutil.which", return_value="/usr/bin/dnf"):
        return DNFCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)


def fedora_os_data(release="41") -> dict[str, str]:
    return {"id": "fedora", "os_family": "RedHat", "release": release}


def fedora_upgrade_os_data(current="41", upgraded="42") -> list[dict]:
    """os_data sequence for a successful run: the pre-flight guard reads the
    current release, the post-upgrade verification reads the new one."""
    return [fedora_os_data(current), fedora_os_data(upgraded)]


@pytest.fixture(autouse=True)
def postinstall_calls(monkeypatch):
    """Mock qubes.PostInstall RPC execution and record invocations."""
    calls: list[list] = []

    def fake_call(cmd, **_kwargs) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(
        "source.common.package_manager.subprocess.call", fake_call
    )
    return calls


# DNFCLI._release_upgrade -- happy path


def test_version_upgrade_runs_clean_then_distro_sync() -> None:
    mgr = make_dnf_cli()
    calls = []

    def fake_run_cmd(cmd, realtime=True) -> ProcessResult:
        calls.append((tuple(cmd), realtime))
        return ProcessResult(EXIT.OK)

    with patch.object(mgr, "run_cmd", side_effect=fake_run_cmd), patch(
        "source.dnf.dnf_cli.get_os_data", side_effect=fedora_upgrade_os_data()
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK
    # Old-release cache is wiped (captured, not streamed) before the bump.
    assert calls[0] == ((mgr.package_manager, "clean", "all"), False)
    sync_cmd, sync_realtime = calls[1]
    assert sync_cmd == _expected_sync_cmd(mgr.package_manager, "42")
    # distro-sync streams in real time so dom0 sees live output.
    assert sync_realtime is True


def test_version_upgrade_emits_progress_milestones(capsys) -> None:
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data", side_effect=fedora_upgrade_os_data()
    ):
        mgr.version_upgrade("42")

    # The progress contract QubeConnection._collect_stderr parses: bare floats
    # terminated by 100.00. 99.50 lands before qubes.PostInstall, whose
    # fstrim runs long enough that reporting 100 first would stall the bar.
    assert capsys.readouterr().err.split() == ["0.00", "99.50", "100.00"]


def test_version_upgrade_skips_duplicate_final_milestone(capsys) -> None:
    """When callback-driven progress (the API subclasses' ProgressReporter)
    already reported 100, the explicit final milestone must not repeat it:
    dom0 treats everything after the first 100 as error text, so a second
    100 surfaces as a bogus "err: 100.00" line."""
    mgr = make_dnf_cli()
    mgr.progress = SimpleNamespace(last_percent=100.0)
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data", side_effect=fedora_upgrade_os_data()
    ):
        mgr.version_upgrade("42")

    assert capsys.readouterr().err.split() == ["0.00"]


def test_version_upgrade_completes_progress_that_fell_short(capsys) -> None:
    """Callback-driven progress that stops shy of 100 (rounding in the
    transaction callbacks) still needs the explicit completion milestone."""
    mgr = make_dnf_cli()
    mgr.progress = SimpleNamespace(last_percent=99.87)
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data", side_effect=fedora_upgrade_os_data()
    ):
        mgr.version_upgrade("42")

    assert capsys.readouterr().err.split() == ["0.00", "100.00"]


def test_progress_reporter_remembers_last_reported_percent() -> None:
    """ProgressReporter records the highest percent it emitted, so
    PackageManager._finish_progress can tell whether 100 already went out."""
    log = MagicMock()
    reporter = ProgressReporter(
        Progress(1, log),
        Progress(1, log),
        Progress(2, log),
        callback=lambda percent: None,
    )
    assert reporter.last_percent == 0.0

    # completing the update phase maps to that phase's stop percent (25)
    reporter.update_progress.notify_callback(100)
    assert reporter.last_percent == 25.0

    reporter.upgrade_progress.notify_callback(100)
    assert reporter.last_percent == 100.0


# ProgressReporter step ranges: one slice of the bar per upgrade step


def _apt_style_reporter():
    """Build a reporter with APT's phase weights."""
    log = MagicMock()
    emitted: list[float] = []
    phases = (Progress(4, log), Progress(48, log), Progress(48, log))
    reporter = ProgressReporter(*phases, callback=emitted.append)
    return reporter, emitted


def test_step_range_keeps_a_repeated_phase_off_100() -> None:
    """APTCLI._release_upgrade runs the phases six times. Without a slice
    per step the second one drives the bar to 100 and the monotonic guard
    silences the remaining four."""
    reporter, _ = _apt_style_reporter()
    reporter.set_step_range(0.0, 20.0, installs=True)
    # every apt commit() ends in finish_update() -> notify_callback(100)
    reporter.upgrade_progress.notify_callback(100)
    assert reporter.last_percent == 20.0

    reporter.set_step_range(20.0, 40.0, installs=True)
    reporter.upgrade_progress.notify_callback(100)
    assert reporter.last_percent == 40.0


def test_step_range_gives_the_whole_slice_to_the_active_phases() -> None:
    """A refresh-only step drives just the update phase, whose weight is a
    small fraction of the reporter. It still has to cross its own slice,
    or the bar sits still for the whole apt update."""
    reporter, _ = _apt_style_reporter()
    reporter.set_step_range(10.0, 30.0, installs=False)
    reporter.update_progress.notify_callback(100)
    assert reporter.last_percent == 30.0

    # an installing step splits its slice between fetch and install
    reporter.set_step_range(30.0, 50.0, installs=True)
    reporter.fetch_progress.notify_callback(100)
    assert reporter.last_percent == 40.0
    reporter.upgrade_progress.notify_callback(100)
    assert reporter.last_percent == 50.0


def _zero_weight_reporter():
    """Build a reporter whose phases all weigh nothing.

    ProgressReporter.__init__ normalizes phase weights, so it cannot be
    constructed directly at total weight 0; start from a valid reporter
    and flatten the weights to reach set_step_range's degenerate case.
    """
    log = MagicMock()
    emitted: list[float] = []
    phases = (Progress(4, log), Progress(48, log), Progress(48, log))
    reporter = ProgressReporter(*phases, callback=emitted.append)
    for phase in phases:
        phase.weight = 0
    return reporter


def test_step_range_splits_evenly_when_all_weights_are_zero() -> None:
    """A degenerate reporter whose active phases all weigh nothing must
    still hand out the slice: split it evenly instead of collapsing every
    phase to a zero-width range at the slice start."""
    reporter = _zero_weight_reporter()

    reporter.set_step_range(10.0, 30.0, installs=True)
    # update is collapsed onto the slice start and must stay silent,
    # while fetch and upgrade split 10..30 evenly
    reporter.update_progress.notify_callback(100)
    assert reporter.last_percent == 0.0
    reporter.fetch_progress.notify_callback(100)
    assert reporter.last_percent == 20.0
    reporter.upgrade_progress.notify_callback(100)
    assert reporter.last_percent == 30.0


def test_step_range_gives_whole_slice_to_a_single_zero_weight_phase() -> None:
    """Same even-split rule for a refresh-only step over a zero-weight
    update phase: the phase gets the entire slice."""
    reporter = _zero_weight_reporter()

    reporter.set_step_range(10.0, 30.0, installs=False)
    reporter.update_progress.notify_callback(100)
    assert reporter.last_percent == 30.0


def test_apt_release_upgrade_bar_stays_below_100_throughout(
    apt_sources, capfd
) -> None:
    """End to end over the real step list: the bar must climb and stop
    short of 100 until qubes.PostInstall has run.

    capfd, not capsys: ProgressReporter dups the real stdout/stderr fds.
    """
    mgr = make_apt_cli()
    mgr.progress, emitted = _apt_style_reporter()

    def drive_step(*_args, **_kwargs) -> ProcessResult:
        # Stand in for a step that runs its phases to completion. Every
        # phase is driven, including the ones the step did not claim, to
        # prove a stale range cannot jump the bar.
        mgr.progress.update_progress.notify_callback(100)
        mgr.progress.fetch_progress.notify_callback(100)
        mgr.progress.upgrade_progress.notify_callback(100)
        return ProcessResult(EXIT.OK)

    with patch.object(mgr, "refresh", side_effect=drive_step), patch.object(
        mgr, "upgrade_internal", side_effect=drive_step
    ), patch.object(mgr, "_dist_upgrade", side_effect=drive_step), patch.object(
        mgr, "_rewrite_sources", side_effect=drive_step
    ), patch.object(
        mgr, "remove_obsolete_kernels", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[
            debian_os_data("12", "bookworm"),
            debian_os_data("13", "trixie"),
        ],
    ):
        assert mgr.version_upgrade("13") == EXIT.OK

    assert emitted == sorted(emitted)
    # the callback stream climbs to, but not past, the pre-PostInstall mark
    assert max(emitted) == RELEASE_UPGRADE_ALMOST_DONE
    # so the explicit milestone is redundant and only 100 is printed
    assert capfd.readouterr().err.split() == ["0.00", "100.00"]


def test_apt_release_upgrade_step_slices_match_the_measured_weights(
    apt_sources,
) -> None:
    """Pin each step's slice of the bar: the weights are 7/7/1/9/22/54
    (shares of a measured debian-12 to 13 upgrade), scaled into
    0..RELEASE_UPGRADE_ALMOST_DONE."""
    mgr = make_apt_cli()
    recorded: list[tuple[float, float, bool]] = []

    def drive_step(*_args, **_kwargs) -> ProcessResult:
        return ProcessResult(EXIT.OK)

    with patch.object(
        mgr, "_set_progress_step", side_effect=lambda *a: recorded.append(a)
    ), patch.object(mgr, "refresh", side_effect=drive_step), patch.object(
        mgr, "upgrade_internal", side_effect=drive_step
    ), patch.object(
        mgr, "_dist_upgrade", side_effect=drive_step
    ), patch.object(
        mgr, "_rewrite_sources", side_effect=drive_step
    ), patch.object(
        mgr, "remove_obsolete_kernels", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[
            debian_os_data("12", "bookworm"),
            debian_os_data("13", "trixie"),
        ],
    ):
        assert mgr.version_upgrade("13") == EXIT.OK

    weights = (
        (7, False),  # refresh before switching sources
        (7, True),  # upgrade current release fully
        (1, False),  # rewrite codename across apt sources
        (9, False),  # refresh onto the new release
        (22, True),  # upgrade onto the new release
        (54, True),  # dist-upgrade across the release boundary
    )
    expected = []
    done = 0
    for weight, installs in weights:
        start = done / 100 * RELEASE_UPGRADE_ALMOST_DONE
        stop = (done + weight) / 100 * RELEASE_UPGRADE_ALMOST_DONE
        expected.append((start, stop, installs))
        done += weight

    assert len(recorded) == len(expected) == 6
    for (got_start, got_stop, got_installs), (
        want_start,
        want_stop,
        want_installs,
    ) in zip(recorded, expected):
        assert got_start == pytest.approx(want_start)
        assert got_stop == pytest.approx(want_stop)
        assert got_installs == want_installs


# ReleaseUpgradeTail: shaping the post-transaction scriptlet tail


def _make_tail(weights=(0, 55, 45)):
    """Build an armed ReleaseUpgradeTail over dnf5's phase weights.

    :return: (tail, emitted) where `emitted` accumulates overall percents
    """

    class Tail(ReleaseUpgradeTail, Progress):
        def __init__(self, weight, log) -> None:
            Progress.__init__(self, weight, log)
            ReleaseUpgradeTail.__init__(self)

    log = MagicMock()
    emitted: list[float] = []
    update, fetch, upgrade = weights
    tail = Tail(upgrade, log)
    ProgressReporter(
        Progress(update, log),
        Progress(fetch, log),
        tail,
        callback=emitted.append,
    )
    tail.open_tail()
    return tail, emitted


def test_tail_inert_until_armed() -> None:
    """Ordinary qubes-vm-update runs must be untouched: with the tail
    closed, package callbacks are neither rescaled nor redirected."""
    tail, emitted = _make_tail()
    tail.close_tail()
    tail.notify_callback(tail._scaled_percent(100))
    # the upgrade phase owns 55..100, so an unscaled 100 reaches 100
    assert emitted == [100.0]
    tail.note_tail_scriptlet()
    assert emitted == [100.0]


def test_tail_rescales_package_progress_to_end_at_cap() -> None:
    """Package callbacks are rescaled, not clamped: clamping pins the bar
    for however long the elements past the cap take, which is the freeze
    this shaping exists to remove."""
    tail, emitted = _make_tail()
    for local in (25, 50, 75, 100):
        tail.notify_callback(tail._scaled_percent(local))

    span = ReleaseUpgradeTail.CAP - 55
    assert emitted == pytest.approx(
        [55 + span * f for f in (0.25, 0.5, 0.75, 1.0)], abs=0.01
    )


def test_tail_never_reaches_its_ceiling() -> None:
    """The scriptlet count cannot be known up front, so the band is
    asymptotic: however many run, the bar stays below TAIL_STOP."""
    tail, emitted = _make_tail()
    for _ in range(500):
        tail.note_tail_scriptlet()

    assert emitted == sorted(emitted)
    assert emitted[0] > ReleaseUpgradeTail.CAP
    assert emitted[-1] < ReleaseUpgradeTail.TAIL_STOP
    assert emitted[-1] > ReleaseUpgradeTail.TAIL_STOP - 0.5


def test_tail_is_visible_on_a_one_decimal_bar() -> None:
    """dom0 renders one decimal, so the band has to be wide enough that a
    realistic scriptlet count produces distinct readings rather than one
    frozen value."""
    tail, emitted = _make_tail()
    # 99 scriptlets, as measured on a fedora-42 to 43 template upgrade
    for _ in range(99):
        tail.note_tail_scriptlet()

    assert len({f"{percent:.1f}" for percent in emitted}) > 50


def test_dnf5_tail_marker_types_are_known() -> None:
    """Verify expected libdnf5 transaction callback script types exist."""
    libdnf5 = pytest.importorskip("libdnf5")
    callbacks = libdnf5.rpm.TransactionCallbacks
    assert isinstance(callbacks.ScriptType_POST_TRANSACTION, int)
    # %postuntrans is rpm 4.20 and up, so absence is tolerated
    from source.dnf.dnf5_api import tail_marker_types

    assert callbacks.ScriptType_POST_TRANSACTION in tail_marker_types()


def test_version_upgrade_final_milestone_follows_postinstall() -> None:
    """100 must come after qubes.PostInstall: its fstrim runs long enough
    that reporting 100 first leaves the bar apparently stuck."""
    mgr = make_dnf_cli()
    order: list[str] = []

    def record_postinstall(_cmd, **_kwargs) -> int:
        order.append("postinstall")
        return 0

    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data", side_effect=fedora_upgrade_os_data()
    ), patch(
        "source.common.package_manager.subprocess.call",
        side_effect=record_postinstall,
    ), patch.object(
        PackageManager,
        "_report_progress",
        staticmethod(lambda percent: order.append(f"{percent:.2f}")),
    ):
        mgr.version_upgrade("42")

    assert order == ["0.00", "99.50", "postinstall", "100.00"]


def test_version_upgrade_release_bump_goes_through_distro_sync_seam() -> None:
    """The release bump must route through `_distro_sync`: the dnf/dnf5 API
    subclasses override that method to report fine-grained progress, so the
    base flow may not bypass it."""
    mgr = make_dnf_cli()
    seam_calls = []

    def fake_distro_sync(target) -> ProcessResult:
        seam_calls.append(target)
        return ProcessResult(EXIT.OK)

    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ) as run_cmd, patch.object(
        mgr, "_distro_sync", side_effect=fake_distro_sync
    ), patch(
        "source.dnf.dnf_cli.get_os_data", side_effect=fedora_upgrade_os_data()
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK
    assert seam_calls == ["42"]
    # only the cache wipe still goes through run_cmd
    assert [tuple(c.args[0]) for c in run_cmd.call_args_list] == [
        (mgr.package_manager, "clean", "all")
    ]


# DNFCLI._release_upgrade -- in-qube re-verification (single-step only)


def test_version_upgrade_refuses_non_numeric_target() -> None:
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
def test_version_upgrade_enforces_single_step(target, current) -> None:
    mgr = make_dnf_cli()
    with patch.object(mgr, "run_cmd") as run_cmd, patch(
        "source.dnf.dnf_cli.get_os_data",
        return_value=fedora_os_data(current),
    ):
        code = mgr.version_upgrade(target)

    assert code == EXIT.ERR_VM_UPDATE
    run_cmd.assert_not_called()


def test_version_upgrade_allows_dotted_current_release() -> None:
    # VERSION_ID like "41.20240101" should compare on the major component.
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ) as run_cmd, patch(
        "source.dnf.dnf_cli.get_os_data",
        side_effect=fedora_upgrade_os_data("41.20240101", "42.20250101"),
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK
    assert run_cmd.call_count == 2


# DNFCLI._release_upgrade -- failure mapping


def test_version_upgrade_bails_when_clean_fails() -> None:
    mgr = make_dnf_cli()
    calls = []

    def fake_run_cmd(cmd, realtime=True) -> ProcessResult:
        calls.append(tuple(cmd))
        return ProcessResult(3)  # clean all fails

    with patch.object(mgr, "run_cmd", side_effect=fake_run_cmd), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.ERR_VM_UPDATE
    # distro-sync is never attempted once the cache wipe fails.
    assert calls == [(mgr.package_manager, "clean", "all")]


def test_version_upgrade_maps_distro_sync_failure() -> None:
    mgr = make_dnf_cli()

    def fake_run_cmd(cmd, realtime=True) -> ProcessResult:
        if "distro-sync" in cmd:
            return ProcessResult(7)  # arbitrary non-zero dnf failure
        return ProcessResult(EXIT.OK)

    with patch.object(mgr, "run_cmd", side_effect=fake_run_cmd), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    # Any non-zero is normalised to a dom0-handled VM error code.
    assert code == EXIT.ERR_VM_UPDATE


# DNFCLI._release_upgrade -- post-upgrade verification


def test_version_upgrade_fails_when_release_did_not_move() -> None:
    """Fail upgrade if os-release version did not change after distro-sync."""
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data",
        side_effect=fedora_upgrade_os_data("41", upgraded="41"),
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.ERR_VM_UPDATE


def test_version_upgrade_fails_when_verification_unreadable() -> None:
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data",
        side_effect=[fedora_os_data("41"), OSError("os-release gone")],
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.ERR_VM_UPDATE


# PackageManager.version_upgrade -- dom0 metadata notification


def test_version_upgrade_notifies_dom0_after_success(
    postinstall_calls,
) -> None:
    """A successful release upgrade must run qubes.PostInstall so dom0's
    qvm-features (os-version/os-eol) and app menus refresh without waiting
    for the next qube start."""
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data",
        side_effect=fedora_upgrade_os_data(),
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK
    assert postinstall_calls == [["/etc/qubes-rpc/qubes.PostInstall"]]


def test_version_upgrade_skips_notification_on_failure(
    postinstall_calls,
) -> None:
    mgr = make_dnf_cli()
    with patch.object(mgr, "run_cmd", return_value=ProcessResult(7)), patch(
        "source.dnf.dnf_cli.get_os_data", return_value=fedora_os_data("41")
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.ERR_VM_UPDATE
    assert postinstall_calls == []


def test_version_upgrade_notification_failure_is_nonfatal(
    monkeypatch,
) -> None:
    """The upgrade itself succeeded; a broken/missing qubes.PostInstall only
    delays the metadata refresh until next boot and must not fail the run."""
    monkeypatch.setattr(
        "source.common.package_manager.subprocess.call",
        MagicMock(side_effect=FileNotFoundError("no such rpc")),
    )
    mgr = make_dnf_cli()
    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)
    ), patch(
        "source.dnf.dnf_cli.get_os_data",
        side_effect=fedora_upgrade_os_data(),
    ):
        code = mgr.version_upgrade("42")

    assert code == EXIT.OK


# Base class -- fail-closed default for families without an implementation


def test_base_version_upgrade_fails_loud() -> None:
    mgr = PackageManager(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    with pytest.raises(NotImplementedError, match="not implemented"):
        mgr._release_upgrade("42")
    with pytest.raises(NotImplementedError, match="not implemented"):
        mgr.version_upgrade("42")


def test_time_operation_logs_only_successful_operations() -> None:
    mgr = PackageManager(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    mgr.log = Mock()
    with patch(
        "source.common.package_manager.time.monotonic",
        side_effect=[10.0, 12.5, 20.0],
    ):
        with mgr._time_operation("resolve release 42"):
            pass
        with pytest.raises(RuntimeError, match="failed"):
            with mgr._time_operation("failed operation"):
                raise RuntimeError("failed")

    mgr.log.debug.assert_called_once_with(
        "%s took %.3fs", "resolve release 42", 2.5
    )


# Agent CLI surface -- args round-trip


def _parse_agent_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)
    return parser.parse_args(argv)


def test_version_upgrade_flag_round_trips_through_cli_args() -> None:
    args = _parse_agent_args(["--version-upgrade", "42"])
    assert args.version_upgrade == "42"

    cli = AgentArgs.to_cli_args(args)
    assert "--version-upgrade" in cli
    assert cli[cli.index("--version-upgrade") + 1] == "42"


def test_version_upgrade_flag_absent_by_default() -> None:
    args = _parse_agent_args([])
    assert args.version_upgrade is None

    cli = AgentArgs.to_cli_args(args)
    assert "--version-upgrade" not in cli
    # Regression guard: a None-valued option must never leak a bare token.
    assert None not in cli


# Entrypoint dispatch


def _patched_entrypoint(pkg_mng) -> tuple:
    """Common patches so entrypoint.main runs without a real qube/logs."""
    fake_logs = (MagicMock(), MagicMock(), logging.DEBUG, "", "")
    return (
        patch("entrypoint.init_logs", return_value=fake_logs),
        patch("entrypoint.get_os_data", return_value=fedora_os_data("41")),
        patch("entrypoint.get_package_manager", return_value=pkg_mng),
        patch("entrypoint.os.system"),
    )


def test_entrypoint_dispatches_to_version_upgrade() -> None:
    pkg_mng = MagicMock()
    pkg_mng.version_upgrade.return_value = EXIT.OK
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main(["--version-upgrade", "42"])

    pkg_mng.version_upgrade.assert_called_once_with("42", print_streams=False)
    pkg_mng.upgrade.assert_not_called()
    assert code == EXIT.OK


def test_entrypoint_maps_missing_version_upgrade_to_handled_error(
    capsys,
) -> None:
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


def test_entrypoint_runs_normal_update_without_flag() -> None:
    pkg_mng = MagicMock()
    pkg_mng.upgrade.return_value = EXIT.OK
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main([])

    pkg_mng.upgrade.assert_called_once()
    pkg_mng.version_upgrade.assert_not_called()
    assert code == EXIT.OK


def test_entrypoint_skips_final_tick_for_version_upgrade(capsys) -> None:
    """A CLI manager's version_upgrade() emits its own 0/100 milestones;
    a second 100.00 from the entrypoint would reach dom0 as a stray
    'err: 100.00' after the run already finished."""
    pkg_mng = MagicMock()
    pkg_mng.PROGRESS_REPORTING = False
    pkg_mng.version_upgrade.return_value = EXIT.OK
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main(["--version-upgrade", "42"])

    assert code == EXIT.OK
    assert "100.00" not in capsys.readouterr().err


def test_entrypoint_prints_final_tick_for_normal_cli_update(capsys) -> None:
    """Without --version-upgrade the fallback finished tick must remain."""
    pkg_mng = MagicMock()
    pkg_mng.PROGRESS_REPORTING = False
    pkg_mng.upgrade.return_value = EXIT.OK
    pkg_mng.clean.return_value = EXIT.OK

    patches = _patched_entrypoint(pkg_mng)
    with patches[0], patches[1], patches[2], patches[3]:
        code = entrypoint.main([])

    assert code == EXIT.OK
    assert "100.00" in capsys.readouterr().err


# APTCLI Debian release-upgrade path


def test_apt_api_refresh_reloads_sources_before_update() -> None:
    apt_api = pytest.importorskip("source.apt.apt_api")
    mgr = apt_api.APT.__new__(apt_api.APT)
    mgr.wait_for_lock = MagicMock()
    mgr.apt_cache = MagicMock()
    mgr.progress = MagicMock()
    mgr.log = MagicMock()
    calls = []

    mgr.apt_cache.open.side_effect = lambda: calls.append("open")

    def update(*_args, **_kwargs) -> bool:
        calls.append("update")
        return True

    mgr.apt_cache.update.side_effect = update

    result = mgr.refresh(hard_fail=True)

    assert result.code == EXIT.OK
    assert calls == ["open", "update", "open"]


def test_apt_api_dist_upgrade_reports_preparation_phase(
    tmp_path, capsys
) -> None:
    apt_api = pytest.importorskip("source.apt.apt_api")
    mgr = apt_api.APT.__new__(apt_api.APT)
    mgr.apt_cache = MagicMock()
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    # apt_pkg.Configuration is a C extension object whose attributes
    # (find_dir, set) are read-only, so patch the module-level apt_pkg
    # reference instead of poking at config directly.
    with patch.object(apt_api, "apt_pkg") as mock_apt_pkg:
        mock_apt_pkg.config.find_dir.return_value = str(tmp_path)
        result = mgr._dist_upgrade()

    assert result.code == EXIT.OK
    assert capsys.readouterr().out.splitlines() == [
        "Preparing distribution upgrade; dependency calculation may take "
        "some time...",
        "Calculating package changes...",
    ]
    mgr.apt_cache.open.assert_called_once_with()
    mgr.apt_cache.upgrade.assert_called_once_with(dist_upgrade=True)
    mgr.apt_cache.commit.assert_called_once()


def test_apt_api_fetch_progress_announces_every_transaction(capsys) -> None:
    """A release upgrade commits three times through one FetchProgress."""
    apt_api = pytest.importorskip("source.apt.apt_api")
    progress = apt_api.FetchProgress(weight=48, log=MagicMock())
    progress.notify_callback = MagicMock()

    for _ in range(3):
        progress.start()
        progress.total_items = 90
        progress.total_bytes = 285733027
        progress.current_bytes = 1024
        progress.pulse(None)
        progress.pulse(None)
        progress.stop()

    out = capsys.readouterr().out
    assert out.count("Fetching 90 packages") == 3


def test_apt_api_fetch_progress_reports_during_long_download(capsys) -> None:
    """The 954 MiB release-upgrade fetch must not go silent for minutes."""
    apt_api = pytest.importorskip("source.apt.apt_api")
    progress = apt_api.FetchProgress(weight=48, log=MagicMock())
    progress.notify_callback = MagicMock()

    # start() takes the first tick, then one per pulse; only the pulses at
    # +30s and +39s past the last report clear REPORT_INTERVAL.
    clock = [0.0, 1.0, 5.0, 31.0, 32.0, 70.0]
    with patch.object(apt_api.time, "monotonic", side_effect=clock):
        progress.start()
        progress.total_items = 979
        progress.total_bytes = 1000.0
        progress.current_bytes = 100.0
        for _ in range(len(clock) - 1):
            progress.pulse(None)

    out = capsys.readouterr().out
    assert out.count("Fetching 979 packages") == 1
    assert out.count("of 1000.00 B...") == 2


# DNF API release-upgrade path


def test_dnf_api_distro_sync_reports_preparation_phase(capsys) -> None:
    """The dnf API _distro_sync must print the same preparation and
    dependency-calculation notices the apt path does, so the user is not
    staring at a blank terminal while fill_sack/resolve runs for minutes."""
    dnf_api = pytest.importorskip("source.dnf.dnf_api")
    mgr = dnf_api.DNF.__new__(dnf_api.DNF)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    # base.transaction is truthy so the empty-transaction early return is
    # skipped; its install_set iterates over nothing so sign_check is a no-op
    base.transaction.install_set = []

    with patch.object(
        dnf_api.dnf.conf, "Conf", return_value=MagicMock()
    ), patch.object(dnf_api.dnf, "Base", return_value=base):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.OK
    out_lines = capsys.readouterr().out.splitlines()
    assert (
        "Preparing distribution upgrade; dependency calculation may "
        "take some time..." in out_lines
    )
    assert "Calculating package changes..." in out_lines

    base.fill_sack.assert_called_once()
    base.distro_sync.assert_called_once()
    base.resolve.assert_called_once_with(allow_erasing=True)
    base.download_packages.assert_called_once()
    base.do_transaction.assert_called_once()


def test_dnf_api_distro_sync_maps_errors_and_closes_base() -> None:
    """A depsolve failure must surface as a handled ERR_VM_UPDATE result
    (so dom0 rolls the clone back) and still close the dnf base."""
    dnf_api = pytest.importorskip("source.dnf.dnf_api")
    mgr = dnf_api.DNF.__new__(dnf_api.DNF)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    base.resolve.side_effect = RuntimeError("depsolve failed")

    with patch.object(
        dnf_api.dnf.conf, "Conf", return_value=MagicMock()
    ), patch.object(dnf_api.dnf, "Base", return_value=base):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.ERR_VM_UPDATE
    assert "depsolve failed" in result.err
    base.close.assert_called_once()
    base.download_packages.assert_not_called()
    base.do_transaction.assert_not_called()


def test_dnf_api_distro_sync_empty_transaction_is_success() -> None:
    """An already-current clone (nothing to sync) is a success, not an
    error, and must not attempt a download or a transaction."""
    dnf_api = pytest.importorskip("source.dnf.dnf_api")
    mgr = dnf_api.DNF.__new__(dnf_api.DNF)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    base.transaction = None

    with patch.object(
        dnf_api.dnf.conf, "Conf", return_value=MagicMock()
    ), patch.object(dnf_api.dnf, "Base", return_value=base):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.OK
    base.download_packages.assert_not_called()
    base.do_transaction.assert_not_called()
    base.close.assert_called_once()


def test_dnf5_api_distro_sync_reports_preparation_phase(capsys) -> None:
    """The libdnf5 API _distro_sync must match the dnf/apt preparation
    notices so every release-upgrade backend gives the same user feedback."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    mgr = dnf5_api.DNF5.__new__(dnf5_api.DNF5)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    repo_sack = MagicMock()
    base.get_repo_sack.return_value = repo_sack

    transaction = MagicMock()
    transaction.get_problems.return_value = (
        dnf5_api.libdnf5.base.GoalProblem_NO_PROBLEM
    )
    transaction.get_transaction_packages_count.return_value = 1
    transaction.check_gpg_signatures.return_value = True
    transaction.run.return_value = transaction.TransactionRunResult_SUCCESS

    goal = MagicMock()
    goal.resolve.return_value = transaction

    with patch.object(
        dnf5_api.libdnf5.base, "Base", return_value=base
    ), patch.object(
        dnf5_api.libdnf5.repo, "DownloadCallbacksUniquePtr"
    ), patch.object(
        dnf5_api.libdnf5.rpm, "TransactionCallbacksUniquePtr"
    ), patch.object(
        dnf5_api, "Goal", return_value=goal
    ):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.OK
    out_lines = capsys.readouterr().out.splitlines()
    assert (
        "Preparing distribution upgrade; dependency calculation may "
        "take some time..." in out_lines
    )
    assert "Calculating package changes..." in out_lines

    repo_sack.load_repos.assert_called_once()
    transaction.download.assert_called_once()
    transaction.run.assert_called_once()


def test_dnf5_api_distro_sync_reports_resolution_failure(capsys) -> None:
    """A failed solve with no transaction packages is an error, not a no-op."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    mgr = dnf5_api.DNF5.__new__(dnf5_api.DNF5)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    transaction = MagicMock()
    transaction.get_problems.return_value = (
        dnf5_api.libdnf5.base.GoalProblem_SOLVER_ERROR
    )
    transaction.get_resolve_logs_as_strings.return_value = [
        "installed rpmfusion-free-release requires system-release(43)"
    ]
    transaction.get_transaction_packages_count.return_value = 0
    goal = MagicMock()
    goal.resolve.return_value = transaction

    with patch.object(
        dnf5_api.libdnf5.base, "Base", return_value=base
    ), patch.object(
        dnf5_api.libdnf5.repo, "DownloadCallbacksUniquePtr"
    ), patch.object(
        dnf5_api, "Goal", return_value=goal
    ):
        result = mgr._distro_sync("44")

    assert result.code == EXIT.ERR_VM_UPDATE
    assert "Failed to resolve package dependencies" in capsys.readouterr().out
    assert "Failed to resolve the distro-sync transaction" in result.err
    assert "rpmfusion-free-release" in result.err
    transaction.download.assert_not_called()
    transaction.run.assert_not_called()


def test_dnf5_fetch_progress_tolerates_unknown_sizes(capsys) -> None:
    """libdnf5 reports unknown download sizes as -1: the fetch header must
    not show a negative total (the fedora-08 run printed "Fetching 4
    packages [-4.00 B]") and the percent math must not divide by zero."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    progress = dnf5_api.FetchProgress(weight=90, log=MagicMock())
    progress.notify_callback = MagicMock()

    for name in ("repo-a", "repo-b", "repo-c", "repo-d"):
        progress.add_new_download(None, name, -1)
    progress.progress(1, -1, 512.0)

    out = capsys.readouterr().out
    assert "Fetching 4 packages [0.00 B]" in out
    assert "Fetching repo-a [unknown size]" in out
    assert "-4.00 B" not in out and "-1.00 B" not in out


def test_dnf5_fetch_percent_does_not_stall_on_a_growing_total() -> None:
    """libdnf5 registers downloads progressively, so accumulating the total
    as they arrive gives a denominator that grows with its own numerator:
    the ratio flattens and, because notify_callback only moves forward, the
    bar sticks. A recorded fedora 41 -> 42 run froze on 28.3% for 10m23s
    that way. Knowing the total up front must keep the percent climbing."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    progress = dnf5_api.FetchProgress(weight=90, log=MagicMock())
    reported: list[float] = []
    progress.notify_callback = reported.append
    progress.expect_bytes(1000.0)

    # Ten packages of 100 B, registered one at a time and each downloaded
    # before the next is announced -- the pattern that used to flatline.
    for i in range(1, 11):
        progress.add_new_download(None, f"pkg-{i}", 100.0)
        progress.progress(i, 100.0, 100.0)

    downloads = [p for p in reported if p > 0]
    assert downloads == sorted(downloads)
    assert downloads[-1] == 100.0
    # The whole point: it climbs throughout instead of pinning early.
    assert len(set(downloads)) == 10


def test_dnf5_expect_bytes_ignores_a_useless_estimate() -> None:
    """An estimate that is absent or too small must degrade to the
    accumulated total, never pin the bar at 100 for the rest of the run."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    progress = dnf5_api.FetchProgress(weight=90, log=MagicMock())
    progress.notify_callback = MagicMock()

    progress.expect_bytes(0.0)  # libdnf5 told us nothing
    progress.add_new_download(None, "pkg", 400.0)
    assert progress.download_total == 400.0

    progress.expect_bytes(100.0)  # an estimate below what we can see
    progress.add_new_download(None, "pkg", 400.0)
    assert progress.download_total == 400.0


def test_dnf5_planned_download_bytes_survives_a_hostile_transaction() -> None:
    """Return 0.0 when transaction package sizes cannot be retrieved."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    broken = MagicMock()
    broken.get_transaction_packages.side_effect = AttributeError("nope")

    assert dnf5_api.planned_download_bytes(broken, MagicMock()) == 0.0


def test_dnf5_planned_download_bytes_skips_erasures(monkeypatch) -> None:
    """Removed packages download nothing; counting them would inflate the
    denominator and strand the bar short of the end."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")

    def item(size: float, inbound: bool) -> MagicMock:
        entry = MagicMock()
        entry.get_action.return_value = inbound
        entry.get_package.return_value.get_download_size.return_value = size
        return entry

    transaction = MagicMock()
    transaction.get_transaction_packages.return_value = [
        item(300.0, True),
        item(999.0, False),
        item(700.0, True),
    ]
    monkeypatch.setattr(
        dnf5_api.libdnf5.base.transaction,
        "transaction_item_action_is_inbound",
        bool,
    )

    assert dnf5_api.planned_download_bytes(transaction, MagicMock()) == 1000.0


def test_dnf5_fetch_mirror_failure_without_metadata(capsys) -> None:
    """libdnf5 passes metadata=None for plain package downloads; the
    mirror-failure notice must not read "Fetching None failure"."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    progress = dnf5_api.FetchProgress(weight=90, log=MagicMock())
    progress.notify_callback = MagicMock()
    progress.add_new_download(None, "openh264-2.5.1", -1)

    progress.mirror_failure(1, "Curl error (28)", "http://mirror", None)

    out = capsys.readouterr().out
    assert "Fetching package failure (openh264-2.5.1) Curl error (28)" in out
    assert "None" not in out


def make_apt_cli() -> APTCLI:
    """Build an APTCLI without requiring a real apt on the host."""
    mgr = APTCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    codenames = {
        "11": "bullseye",
        "12": "bookworm",
        "13": "trixie",
        "14": "forky",
        "15": "duke",
    }
    mgr._debian_codename = codenames.get
    return mgr


def debian_os_data(release="12", codename="bookworm") -> dict[str, str]:
    return {
        "id": "debian",
        "os_family": "Debian",
        "release": release,
        "codename": codename,
    }


# PACMANCLI release-upgrade refusal (Arch is a rolling release)


def test_pacman_release_upgrade_refuses() -> None:
    """--version-upgrade on an Arch template must fail loudly rather than
    fall through to the generic update path."""
    from source.pacman.pacman_cli import PACMANCLI

    mgr = PACMANCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    with pytest.raises(NotImplementedError):
        mgr._release_upgrade("rolling")


# APTCLI in-qube guard (single-step, current codename must be present)


def _apt_guard(os_data, target) -> ProcessResult:
    return make_apt_cli()._verify_release_upgrade(target, os_data)


def test_apt_guard_does_not_recheck_os_family() -> None:
    os_data = debian_os_data("12", "bookworm")
    os_data["os_family"] = "Unknown"
    assert _apt_guard(os_data, "13").code == EXIT.OK


def test_apt_guard_refuses_non_numeric_target() -> None:
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
def test_apt_guard_enforces_single_step(target, current) -> None:
    result = _apt_guard(debian_os_data(current, "bookworm"), target)
    assert result.code == EXIT.ERR_VM_UPDATE


def test_apt_guard_refuses_missing_codename() -> None:
    os_data = {"id": "debian", "os_family": "Debian", "release": "12"}
    assert _apt_guard(os_data, "13").code == EXIT.ERR_VM_UPDATE


def test_apt_guard_passes_for_single_step_debian() -> None:
    assert _apt_guard(debian_os_data("12", "bookworm"), "13").code == EXIT.OK


def test_apt_reads_codename_from_distro_info(tmp_path) -> None:
    releases = tmp_path / "debian.csv"
    # Mirrors /usr/share/distro-info/debian.csv: unreleased rows end
    # after "created" with no trailing commas, sid has no version.
    releases.write_text(
        "version,codename,series,created,release,eol,eol-lts,eol-elts\n"
        "14,Forky,forky,2025-08-09,2027-06-10,2030-06-10,2032-06-30,"
        "2037-06-30\n"
        "15,Duke,duke,2027-08-01\n"
        "16,Ace,ace,2029-08-01,,\n"
        ",Sid,sid,1993-08-16\n"
    )

    with patch.object(APTCLI, "DEBIAN_RELEASES_FILE", str(releases)):
        assert APTCLI._debian_codename("14") == "forky"
        # rows without a release date are unreleased (testing); offering
        # them would dist-upgrade onto testing and then fail verification
        assert APTCLI._debian_codename("15") is None
        assert APTCLI._debian_codename("16") is None
        assert APTCLI._debian_codename("17") is None


# APTCLI._release_upgrade composition (apt mocked at the step level)


def _record_apt_steps(
    mgr, calls: list[str], fail_on=None, cleanup_fail=False
) -> None:
    """Replace the composed steps with recorders."""

    def refresh(hard_fail) -> ProcessResult:
        calls.append("refresh")
        return ProcessResult(EXIT.ERR if fail_on == "refresh" else EXIT.OK)

    def upgrade_internal(remove_obsolete) -> ProcessResult:
        calls.append("upgrade")
        return ProcessResult(EXIT.ERR if fail_on == "upgrade" else EXIT.OK)

    def dist_upgrade() -> ProcessResult:
        calls.append("dist-upgrade")
        return ProcessResult(EXIT.ERR if fail_on == "dist-upgrade" else EXIT.OK)

    def rewrite(old_codename, new_codename) -> ProcessResult:
        calls.append(f"rewrite:{old_codename}->{new_codename}")
        return ProcessResult(EXIT.ERR if fail_on == "rewrite" else EXIT.OK)

    def remove_obsolete_kernels() -> ProcessResult:
        calls.append("kernel-cleanup")
        return ProcessResult(EXIT.ERR_VM_CLEANUP if cleanup_fail else EXIT.OK)

    mgr.refresh = refresh
    mgr.upgrade_internal = upgrade_internal
    mgr._dist_upgrade = dist_upgrade
    mgr._rewrite_sources = rewrite
    mgr.remove_obsolete_kernels = remove_obsolete_kernels


@pytest.fixture
def apt_sources(tmp_path):
    """A sources.list naming the old codename, so _release_upgrade's
    read-only pre-scan passes instead of reading the host's /etc/apt."""
    sources = tmp_path / "sources.list"
    sources.write_text("deb https://deb.debian.org/debian bookworm main\n")
    with patch.object(APTCLI, "APT_SOURCE_GLOBS", (str(sources),)):
        yield sources


def test_apt_release_upgrade_happy_path_order(apt_sources, capsys) -> None:
    mgr = make_apt_cli()
    calls: list[str] = []
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
    assert capsys.readouterr().err.split() == ["0.00", "99.50", "100.00"]


def test_apt_release_upgrade_allows_external_source_without_codename(
    apt_sources,
) -> None:
    external = apt_sources.parent / "external.sources"
    external.write_text(
        "Types: deb\n"
        "URIs: http://localhost/external\n"
        "Suites: stable\n"
        "Components: main\n"
    )
    mgr = make_apt_cli()
    calls: list[str] = []
    _record_apt_steps(mgr, calls)

    with patch.object(
        APTCLI, "APT_SOURCE_GLOBS", (str(apt_sources), str(external))
    ), patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[
            debian_os_data("12", "bookworm"),
            debian_os_data("13", "trixie"),
        ],
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.OK
    assert "dist-upgrade" in calls


def test_apt_release_upgrade_refuses_symbolic_sources(
    apt_sources, capsys
) -> None:
    """Sources addressing the release symbolically (e.g. ``stable``)
    mention no codename; the read-only pre-scan must refuse before the
    refresh and full upgrade run, not after them."""
    apt_sources.write_text("deb https://deb.debian.org/debian stable main\n")
    mgr = make_apt_cli()
    calls: list[str] = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data",
        return_value=debian_os_data("12", "bookworm"),
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.ERR_VM_UPDATE
    assert calls == []


def test_apt_release_upgrade_can_be_retried_after_interruption(
    apt_sources, capsys
) -> None:
    """A run interrupted after the sources rewrite left them naming only
    the new codename; the pre-scan must let the retry proceed."""
    apt_sources.write_text("deb https://deb.debian.org/debian trixie main\n")
    mgr = make_apt_cli()
    calls: list[str] = []
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
    assert "rewrite:bookworm->trixie" in calls


def test_apt_release_upgrade_survives_kernel_cleanup_failure(
    apt_sources, capsys
) -> None:
    # kernel cleanup failure after a successful release bump should not fail the upgrade
    mgr = make_apt_cli()
    calls: list[str] = []
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
    assert capsys.readouterr().err.split() == ["0.00", "99.50", "100.00"]


def test_apt_release_upgrade_bails_before_rewrite_when_update_fails(
    apt_sources, capsys
) -> None:
    mgr = make_apt_cli()
    calls: list[str] = []
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


def test_apt_release_upgrade_maps_dist_upgrade_failure(
    apt_sources, capsys
) -> None:
    mgr = make_apt_cli()
    calls: list[str] = []
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
def test_apt_release_upgrade_verifies_target_release(
    actual, apt_sources, capsys
) -> None:
    mgr = make_apt_cli()
    calls: list[str] = []
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


def test_apt_release_upgrade_guard_failure_short_circuits(capsys) -> None:
    mgr = make_apt_cli()
    calls: list[str] = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data",
        return_value=debian_os_data("12", ""),
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.ERR_VM_UPDATE
    assert not calls  # no apt call, no rewrite
    assert not capsys.readouterr().err  # no progress emitted


def test_apt_release_upgrade_refuses_unknown_target_codename(capsys) -> None:
    mgr = make_apt_cli()
    calls: list[str] = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data",
        return_value=debian_os_data("15", "duke"),
    ):
        code = mgr.version_upgrade("16")

    assert code == EXIT.ERR_VM_UPDATE
    assert not calls
    assert not capsys.readouterr().err


def test_apt_release_upgrade_refuses_unreadable_release_data(capsys) -> None:
    mgr = make_apt_cli()
    mgr._debian_codename = MagicMock(side_effect=OSError("missing"))
    calls: list[str] = []
    _record_apt_steps(mgr, calls)
    with patch(
        "source.apt.apt_cli.get_os_data",
        return_value=debian_os_data("12", "bookworm"),
    ):
        code = mgr.version_upgrade("13")

    assert code == EXIT.ERR_VM_UPDATE
    assert not calls
    assert not capsys.readouterr().err


# apt sources codename rewrite


def test_apt_rewrites_list_and_deb822_sources(tmp_path) -> None:
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
    assert not list(tmp_path.rglob("*.tmp"))


def test_apt_rewrite_refuses_when_no_source_uses_the_codename(tmp_path) -> None:
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
    assert result.err.endswith(
        "no apt source references codename 'bookworm'; refusing upgrade."
    )
    assert main.read_text() == before  # nothing written


def test_apt_rewrite_accepts_already_rewritten_sources(tmp_path) -> None:
    # a run interrupted after the rewrite leaves sources naming only the
    # new codename: the retry's rewrite pass must count them as done, not
    # refuse the whole upgrade
    mgr = make_apt_cli()
    main = tmp_path / "sources.list"
    main.write_text("deb http://deb.debian.org/debian trixie main\n")
    before = main.read_text()
    with patch.object(APTCLI, "APT_SOURCE_GLOBS", (str(main),)):
        result = mgr._rewrite_sources("bookworm", "trixie")

    assert result.code == EXIT.OK
    assert main.read_text() == before  # no spurious write


def test_version_upgrade_prints_unposted_streams(capsys) -> None:
    """Print unposted output and error streams when print_streams is requested."""
    mgr = make_dnf_cli()
    res = ProcessResult(EXIT.OK, out="stdout line\n", err="stderr line\n")
    res.posted = False
    with patch.object(mgr, "_release_upgrade", return_value=res):
        code = mgr.version_upgrade("42", print_streams=True)
    assert code == EXIT.OK
    captured = capsys.readouterr()
    assert "stdout line" in captured.out
    assert "stderr line" in captured.err


def test_verify_release_upgrade_rejects_non_numeric_release() -> None:
    """Reject in-qube release that is not numeric."""
    mgr = make_dnf_cli()
    result = mgr._verify_release_upgrade("42", {"release": "rawhide"})
    assert result.code == EXIT.ERR_VM_UPDATE
    assert "cannot read a numeric in-qube release" in result.err


def test_tail_scaled_percent_clamping_and_zero_span() -> None:
    """_scaled_percent handles out-of-bounds local cap and non-positive spans."""
    tail = ReleaseUpgradeTail()
    tail.log = MagicMock()
    tail.notify_callback = MagicMock()
    # span <= 0 branch in _local_for_overall
    tail._start_percent = 50.0
    tail._stop_percent = 50.0
    assert tail._local_for_overall(50.0) == 100.0

    # cap clamping branch in _scaled_percent: when CAP (90.0) is below _start_percent (95.0)
    tail._start_percent = 95.0
    tail._stop_percent = 100.0
    tail.open_tail()
    scaled = tail._scaled_percent(50.0)
    assert scaled == 0.0
    tail.log.debug.assert_called_once()


def test_progress_reporter_default_emit_to_stderr() -> None:
    """ProgressReporter defaults to emitting formatted percentage to stderr."""
    prog1 = Progress(10, MagicMock())
    prog2 = Progress(10, MagicMock())
    prog3 = Progress(10, MagicMock())
    reporter = ProgressReporter(prog1, prog2, prog3, callback=None)
    with patch("builtins.print") as mock_print:
        reporter.callback(50.0)
    mock_print.assert_called_once_with(
        "50.00", flush=True, file=reporter.stderr
    )
    assert reporter.last_percent == 50.0


def test_dnf_cli_release_upgrade_refuses_yum() -> None:
    """DNFCLI refuses release upgrade when running on yum."""
    mgr = make_dnf_cli()
    mgr.package_manager = "yum"
    result = mgr._release_upgrade("42")
    assert result.code == EXIT.ERR_VM_UPDATE
    assert "release upgrade requires dnf." in result.err


def test_dnf_cli_release_upgrade_handles_os_data_oserror() -> None:
    """DNFCLI handles OSError while reading os-release."""
    mgr = make_dnf_cli()
    with patch(
        "source.dnf.dnf_cli.get_os_data",
        side_effect=OSError("permission denied"),
    ):
        result = mgr._release_upgrade("42")
    assert result.code == EXIT.ERR_VM_UPDATE
    assert "cannot read os-release" in result.err


def test_dnf_api_upgrade_progress() -> None:
    """UpgradeProgress in dnf_api reports scaled percentages on upgrade actions."""
    dnf_api = pytest.importorskip("source.dnf.dnf_api")
    log = MagicMock()
    progress = dnf_api.UpgradeProgress(weight=50, log=log)
    emitted: list[float] = []
    progress.init(0.0, 100.0, emitted.append, MagicMock(), MagicMock())
    progress.open_tail()

    # Ignored action
    progress.progress("pkg-1", 1, 10, 100, 1, 10)
    assert len(emitted) == 0

    # Action 6 (UPGRADE)
    progress.progress("pkg-2", 6, 50, 100, 1, 2)
    assert len(emitted) == 1

    # Action 7 (UPGRADED cleanup)
    progress.progress("pkg-2", 7, 100, 100, 2, 2)
    assert len(emitted) == 2

    # scriptout and filelog
    progress.scriptout(b"script output\n")
    progress.scriptout("str output\n")
    with patch.dict(dnf_api.dnf.transaction.FILE_ACTIONS, {1: "created"}):
        progress.filelog("pkg-2", 1)


def test_package_manager_and_dnf_cli_clean() -> None:
    """clean() defaults to ERR_VM_CLEANUP on base and runs dnf clean on DNFCLI."""
    mgr = make_dnf_cli()
    assert PackageManager.clean(mgr) == EXIT.ERR_VM_CLEANUP

    with patch.object(mgr, "run_cmd", return_value=ProcessResult(EXIT.OK)):
        assert mgr.clean() == 0

    with patch.object(
        mgr, "run_cmd", return_value=ProcessResult(EXIT.ERR_VM_CLEANUP)
    ):
        assert mgr.clean() == EXIT.ERR_VM_CLEANUP


def test_apt_cli_release_upgrade_handles_sources_oserror() -> None:
    """APTCLI handles OSError when reading apt sources."""
    mgr = make_apt_cli()
    with patch(
        "source.apt.apt_cli.get_os_data",
        side_effect=[
            debian_os_data("12", "bookworm"),
            debian_os_data("13", "trixie"),
        ],
    ), patch.object(
        APTCLI,
        "_apt_source_paths",
        return_value=["/etc/apt/sources.list.d/bad.list"],
    ), patch(
        "builtins.open", side_effect=OSError("sources unreadable")
    ):
        result = mgr._release_upgrade("13")
    assert result.code == EXIT.ERR_VM_UPDATE
    assert "cannot read apt sources" in result.err


def test_apt_cli_release_upgrade_handles_post_upgrade_oserror() -> None:
    """APTCLI handles OSError when reading upgraded os-release."""
    mgr = make_apt_cli()
    os_data_calls = [
        debian_os_data("12", "bookworm"),
        OSError("os-release corrupted"),
    ]

    def fake_os_data(_log):
        val = os_data_calls.pop(0)
        if isinstance(val, Exception):
            raise val
        return val

    with patch(
        "source.apt.apt_cli.get_os_data", side_effect=fake_os_data
    ), patch.object(
        APTCLI,
        "_apt_source_paths",
        return_value=["/etc/apt/sources.list"],
    ), patch(
        "builtins.open",
        mock_open(read_data="deb http://deb.debian.org/debian bookworm main\n"),
    ), patch.object(
        mgr, "refresh", return_value=ProcessResult(EXIT.OK)
    ), patch.object(
        mgr, "upgrade_internal", return_value=ProcessResult(EXIT.OK)
    ), patch.object(
        mgr, "_rewrite_sources", return_value=ProcessResult(EXIT.OK)
    ), patch.object(
        mgr, "_dist_upgrade", return_value=ProcessResult(EXIT.OK)
    ):
        result = mgr._release_upgrade("13")
    assert result.code == EXIT.ERR_VM_UPDATE
    assert "failed to verify the upgraded Debian release" in result.err


def test_apt_cli_dist_upgrade_calls_package_manager_upgrade_internal() -> None:
    """_dist_upgrade delegates to PackageManager.upgrade_internal with remove_obsolete=True."""
    mgr = make_apt_cli()
    with patch.object(
        PackageManager, "upgrade_internal", return_value=ProcessResult(EXIT.OK)
    ) as mock_up:
        result = mgr._dist_upgrade()
    assert result.code == EXIT.OK
    mock_up.assert_called_once_with(mgr, remove_obsolete=True)


def test_apt_cli_rewrite_sources_handles_oserror(tmp_path) -> None:
    """_rewrite_sources handles OSError during atomic replacement."""
    mgr = make_apt_cli()
    main = tmp_path / "sources.list"
    main.write_text("deb http://deb.debian.org/debian bookworm main\n")
    with patch.object(APTCLI, "APT_SOURCE_GLOBS", (str(main),)), patch(
        "os.replace", side_effect=OSError("filesystem read-only")
    ):
        result = mgr._rewrite_sources("bookworm", "trixie")
    assert result.code == EXIT.ERR_VM_UPDATE
    assert "failed rewriting apt sources" in result.err


def test_apt_api_upgrade_internal_and_progress() -> None:
    """APT API upgrade_internal handles obsolete removal and UpgradeProgress reporting."""
    apt_api = pytest.importorskip("source.apt.apt_api")
    mgr = apt_api.APT.__new__(apt_api.APT)
    mgr.log = MagicMock()
    mgr.apt_cache = MagicMock()
    with patch.object(
        mgr, "_api_upgrade", return_value=ProcessResult(EXIT.OK)
    ), patch.object(
        mgr,
        "remove_obsolete_kernels",
        return_value=ProcessResult(EXIT.OK),
    ) as mock_rm:
        res1 = mgr.upgrade_internal(remove_obsolete=True)
        assert res1.code == EXIT.OK
        mock_rm.assert_called_once()

        res2 = mgr.upgrade_internal(remove_obsolete=False)
        assert res2.code == EXIT.OK

    progress = apt_api.UpgradeProgress(weight=50, log=mgr.log)
    emitted: list[float] = []
    progress.init(0.0, 100.0, emitted.append, MagicMock(), MagicMock())
    progress.open_tail()
    progress.status_change("pkg", 50.0, "status")
    assert len(emitted) == 1
    progress.finish_update()
    assert len(emitted) == 2

    # test _api_upgrade error handling
    mgr.apt_cache.commit.side_effect = RuntimeError("commit failed")
    mgr.progress = MagicMock()
    err_res = mgr._api_upgrade(dist_upgrade=False)
    assert err_res.code == EXIT.ERR_VM_UPDATE
    assert "commit failed" in err_res.err


def test_dnf5_api_distro_sync_nothing_to_do() -> None:
    """_distro_sync returns OK when transaction package count is 0."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    mgr = dnf5_api.DNF5.__new__(dnf5_api.DNF5)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    repo_sack = MagicMock()
    base.get_repo_sack.return_value = repo_sack
    transaction = MagicMock()
    transaction.get_problems.return_value = (
        dnf5_api.libdnf5.base.GoalProblem_NO_PROBLEM
    )
    transaction.get_transaction_packages_count.return_value = 0
    goal = MagicMock()
    goal.resolve.return_value = transaction

    with patch.object(
        dnf5_api.libdnf5.base, "Base", return_value=base
    ), patch.object(
        dnf5_api.libdnf5.repo, "DownloadCallbacksUniquePtr"
    ), patch.object(
        dnf5_api, "Goal", return_value=goal
    ):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.OK
    mgr.log.info.assert_called_with("Distro-sync found nothing to do.")


def test_dnf5_api_distro_sync_gpg_failure() -> None:
    """_distro_sync raises TransactionError when GPG signature checks fail."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    mgr = dnf5_api.DNF5.__new__(dnf5_api.DNF5)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    repo_sack = MagicMock()
    base.get_repo_sack.return_value = repo_sack
    transaction = MagicMock()
    transaction.get_problems.return_value = (
        dnf5_api.libdnf5.base.GoalProblem_NO_PROBLEM
    )
    transaction.get_transaction_packages_count.return_value = 1
    transaction.check_gpg_signatures.return_value = False
    transaction.get_gpg_signature_problems.return_value = "bad key"
    goal = MagicMock()
    goal.resolve.return_value = transaction

    with patch.object(
        dnf5_api.libdnf5.base, "Base", return_value=base
    ), patch.object(
        dnf5_api.libdnf5.repo, "DownloadCallbacksUniquePtr"
    ), patch.object(
        dnf5_api, "Goal", return_value=goal
    ):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.ERR_VM_UPDATE
    assert "GPG signatures check failed" in result.err


def test_dnf5_api_distro_sync_transaction_run_failure() -> None:
    """_distro_sync raises TransactionError when transaction.run() fails."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    mgr = dnf5_api.DNF5.__new__(dnf5_api.DNF5)
    mgr.progress = MagicMock()
    mgr.log = MagicMock()

    base = MagicMock()
    repo_sack = MagicMock()
    base.get_repo_sack.return_value = repo_sack
    transaction = MagicMock()
    transaction.get_problems.return_value = (
        dnf5_api.libdnf5.base.GoalProblem_NO_PROBLEM
    )
    transaction.get_transaction_packages_count.return_value = 1
    transaction.check_gpg_signatures.return_value = True
    transaction.run.return_value = 99  # not SUCCESS
    transaction.transaction_result_to_string.return_value = "Transaction failed"
    goal = MagicMock()
    goal.resolve.return_value = transaction

    with patch.object(
        dnf5_api.libdnf5.base, "Base", return_value=base
    ), patch.object(
        dnf5_api.libdnf5.repo, "DownloadCallbacksUniquePtr"
    ), patch.object(
        dnf5_api.libdnf5.rpm, "TransactionCallbacksUniquePtr"
    ), patch.object(
        dnf5_api, "Goal", return_value=goal
    ):
        result = mgr._distro_sync("42")

    assert result.code == EXIT.ERR_VM_UPDATE
    assert "Transaction failed" in result.err


def test_dnf5_upgrade_progress_callbacks() -> None:
    """UpgradeProgress in dnf5_api handles install/uninstall/elem/script progress."""
    dnf5_api = pytest.importorskip("source.dnf.dnf5_api")
    progress = dnf5_api.UpgradeProgress(weight=50, log=MagicMock())
    emitted: list[float] = []
    progress.init(0.0, 100.0, emitted.append, MagicMock(), MagicMock())
    progress.open_tail()
    progress.transaction_start(10)

    # install and uninstall use distinct per-package progress so each emits a
    # distinct, strictly-increasing percent through Progress.notify_callback.
    pkg = MagicMock()
    progress.install_progress(pkg, 10, 100)
    assert len(emitted) == 1

    progress.uninstall_progress(pkg, 90, 100)
    assert len(emitted) == 2

    progress.elem_progress(5, 10)
    assert len(emitted) == 3

    # script_start with post-transaction marker
    target_type = next(iter(dnf5_api.TAIL_MARKER_TYPES), 1)
    nevra = MagicMock()
    nevra.get_name.return_value = "pkg"
    progress.script_start(pkg, nevra, target_type)
    assert progress.tail_reached is True
    assert len(emitted) == 4

    # progress is monotonic non-decreasing and stays capped below 100 (the
    # post-transaction tail must never report a final 100 before PostInstall).
    assert emitted == sorted(emitted)
    assert emitted[0] > 0.0
    assert emitted[-1] < 100.0
