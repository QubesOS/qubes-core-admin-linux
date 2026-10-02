# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022  Piotr Bartman <prbartman@invisiblethingslab.com>
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

import io
import os
import sys
from typing import TYPE_CHECKING, Callable, Optional
from logging import Logger


class Progress:
    def __init__(
        self,
        weight: int,
        log: Logger,
    ) -> None:
        self.weight = weight
        self._callback: Optional[Callable[[float], None]] = None
        self._start_percent: Optional[float] = None
        self._stop_percent: Optional[float] = None
        self._last_percent: Optional[float] = None
        self._stdout: Optional[io.TextIOWrapper] = None
        self._stderr: Optional[io.TextIOWrapper] = None
        self.log: Logger = log

    def init(
        self,
        start: float,
        stop: float,
        callback: Callable[[float], None],
        stdout: io.TextIOWrapper,
        stderr: io.TextIOWrapper,
    ) -> None:
        self._callback = callback
        self._start_percent = start
        self._stop_percent = stop
        self._last_percent = start
        self._stdout = stdout
        self._stderr = stderr

    def notify_callback(self, percent: float) -> None:
        """
        Report ongoing progress.
        """
        assert self._last_percent is not None  # call init() first!
        assert self._start_percent is not None  # call init() first!
        assert self._stop_percent is not None  # call init() first!
        assert self._callback is not None  # call init() first!
        _percent = (
            self._start_percent
            + percent * (self._stop_percent - self._start_percent) / 100
        )
        _percent = round(_percent, 2)
        if self._last_percent < _percent:
            self._callback(_percent)
            self._last_percent = _percent

    @staticmethod
    def _format_bytes(size: int | float) -> str:
        units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        factor = 1024
        for unit in units:
            if size < factor:
                return f"{size:.2f} {unit}"
            size /= factor
        return f"{size:.2f} {units[-1]}"


class ReleaseUpgradeTail:
    """Progress shaping for post-transaction scriptlets during release upgrades.

    Rescales package progress to 0..CAP, then asymptotically advances
    from CAP to TAIL_STOP as post-transaction scriptlets execute.
    TAIL_STOP < 100 because qubes.PostInstall still runs afterwards.

    Mixin for Progress subclasses. Inert until open_tail() is called.
    """

    # A measured Fedora 42-to-43 run spent about 13% of its wall time after
    # the package callbacks ended. Reserving ten points keeps that work visible
    # in dom0's one-decimal progress display without overstating the sample.
    CAP = 90.0
    # Reserve the last half point for qubes.PostInstall, including fstrim, so
    # the bar reaches 100 only after all post-upgrade work has completed.
    TAIL_STOP = 99.5
    # The measured tail contained trigger batches of 25 and 29 callbacks; 25
    # therefore puts a representative batch halfway through the asymptote.
    ASYMPTOTE_HALFWAY = 25

    if TYPE_CHECKING:
        # Supplied by the Progress this mixin is combined with, declared
        # here only for the type checker.
        _start_percent: Optional[float]
        _stop_percent: Optional[float]
        log: Logger

        def notify_callback(self, percent: float) -> None: ...

    def __init__(self) -> None:
        self._tail_open = False
        self._tail_seen = 0
        self._tail_clamp_logged = False

    def open_tail(self) -> None:
        """
        Arm tail shaping for the transaction about to run.
        """
        self._tail_open = True
        self._tail_seen = 0

    def close_tail(self) -> None:
        """
        Disarm tail shaping.
        """
        self._tail_open = False

    def _scaled_percent(self, local_percent: float) -> float:
        """Rescale local phase percentage so package progress caps at CAP."""
        if not self._tail_open:
            return local_percent
        cap_local = self._local_for_overall(self.CAP)
        clamped = min(max(cap_local, 0.0), 100.0)
        if clamped != cap_local and not self._tail_clamp_logged:
            self._tail_clamp_logged = True
            self.log.debug(
                "tail CAP maps outside the phase slice (%.2f), clamping",
                cap_local,
            )
        return min(local_percent, 100.0) * clamped / 100

    def _local_for_overall(self, overall: float) -> float:
        """
        Convert a reporter-scale percent into this phase's local percent.

        ProgressReporter.set_step_range() may narrow the phase, so the
        bands are relative to the phase's current slice of the bar.
        """
        assert self._start_percent is not None  # call init() first!
        assert self._stop_percent is not None  # call init() first!
        span = self._stop_percent - self._start_percent
        if span <= 0:
            return 100.0
        return (overall - self._start_percent) / span * 100

    def note_tail_scriptlet(self) -> None:
        """
        Advance the bar for one post-transaction scriptlet.
        """
        if not self._tail_open:
            return
        self._tail_seen += 1
        overall = self.CAP + (self.TAIL_STOP - self.CAP) * self._tail_seen / (
            self._tail_seen + self.ASYMPTOTE_HALFWAY
        )
        self.notify_callback(min(self._local_for_overall(overall), 100.0))


class ProgressReporter:
    """
    Simple rough progress reporter.

    It is assumed that updating, fetching and installing
     takes fixed value of total time.
    """

    def __init__(
        self,
        update: Progress,
        fetch: Progress,
        upgrade: Progress,
        callback: Optional[Callable[[float], None]] = None,
    ) -> None:
        saved_stdout = os.dup(sys.stdout.fileno())
        saved_stderr = os.dup(sys.stderr.fileno())
        self.stdout = io.TextIOWrapper(os.fdopen(saved_stdout, "wb"))
        self.stderr = io.TextIOWrapper(os.fdopen(saved_stderr, "wb"))
        self.last_percent = 0.0
        if callback is None:
            emit: Callable[[float], None] = lambda p: print(
                f"{p:.2f}", flush=True, file=self.stderr
            )
        else:
            emit = callback

        def callback_with_memory(percent: float) -> None:
            # Track highest reported progress to prevent duplicate milestones.
            self.last_percent = max(self.last_percent, percent)
            emit(percent)

        self.callback = callback_with_memory

        total = update.weight + fetch.weight + upgrade.weight
        update_end = update.weight / total * 100
        fetch_end = fetch.weight / total * 100 + update_end

        update.init(0, update_end, self.callback, self.stdout, self.stderr)
        fetch.init(
            update_end, fetch_end, self.callback, self.stdout, self.stderr
        )
        upgrade.init(fetch_end, 100, self.callback, self.stdout, self.stderr)

        self.update_progress = update
        self.fetch_progress = fetch
        self.upgrade_progress = upgrade

    def set_step_range(self, start: float, stop: float, installs: bool) -> None:
        """Allocate a progress range to one release-upgrade step."""
        active = (
            (self.fetch_progress, self.upgrade_progress)
            if installs
            else (self.update_progress,)
        )
        total = sum(phase.weight for phase in active)
        at = start
        for phase in active:
            # With all active weights zero, split the slice evenly rather
            # than collapsing every phase to a zero-width range.
            share = phase.weight / total if total else 1 / len(active)
            span = (stop - start) * share
            phase.init(at, at + span, self.callback, self.stdout, self.stderr)
            at += span
        # Reset inactive phases to start so they don't emit stale progress.
        for phase in (
            self.update_progress,
            self.fetch_progress,
            self.upgrade_progress,
        ):
            if phase not in active:
                phase.init(
                    start, start, self.callback, self.stdout, self.stderr
                )
