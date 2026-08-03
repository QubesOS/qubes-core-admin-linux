# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022-2025  Piotr Bartman-Szwarc
#                                   <prbartman@invisiblethingslab.com>
# Copyright (C) 2022  Marek Marczykowski-Górecki
#                                   <marmarek@invisiblethingslab.com>
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

import argparse
import os
import signal
import sys
import queue
import logging
import multiprocessing
import multiprocessing.managers
from logging import Logger
from os.path import join
from types import FrameType
from typing import Any, Optional, Tuple, Callable, Union

from tqdm import tqdm

from qubesadmin.app import QubesBase
from qubesadmin.vm import QubesVM
from vmupdate.agent.source.log_config import init_logs
from vmupdate.agent.source.common.process_result import ProcessResult
from vmupdate.agent.source.common.exit_codes import EXIT
from .agent.source.status import StatusInfo, FinalStatus, Status, FormatedLine
from .qube_connection import QubeConnection


class UpdateManager:
    """
    Update multiple qubes simultaneously.
    """

    def __init__(
        self,
        qubes: list[QubesVM],
        args: argparse.Namespace,
        log: Logger,
        dom0: bool = False,
    ) -> None:
        self.qubes = qubes
        self.max_concurrency = args.max_concurrency
        self.show_output = args.show_output
        self.quiet = args.quiet
        self.no_progress = args.no_progress
        self.just_print_progress = args.just_print_progress
        self.download_only = args.download_only
        self.buffered = not args.just_print_progress and not args.no_progress
        self.buffer = ""
        self.cleanup = not args.no_cleanup
        self.ret_code = EXIT.OK
        self.log = log
        self.dom0 = dom0

    def run(self, agent_args: argparse.Namespace) -> tuple[int, dict]:
        """
        Run simultaneously `update_qube` for all qubes as separate processes.
        """
        self.log.info("Update Manager: New batch of qubes to update")
        if not self.qubes:
            self.log.info("Update Manager: No qubes to update, quiting.")
            return EXIT.OK, {}

        show_progress = not self.quiet and not self.no_progress
        SimpleTerminalBar.reinit_class(self.download_only)
        progress_output = (
            SimpleTerminalBar if self.just_print_progress else tqdm
        )
        progress_bar = MultipleUpdateMultipleProgressBar(
            dummy=not show_progress,
            output=progress_output,
            max_concurrency=self.max_concurrency,
            printer=self.print if self.show_output else None,
        )

        for qube in self.qubes:
            disp_name = (
                agent_args.display_name
                if agent_args.display_name is not None
                else qube.name
            )
            progress_bar.add_bar(disp_name)
            progress_bar.pool.apply_async(
                update_qube,
                (
                    qube,
                    agent_args,
                    show_progress,
                    progress_bar.status_notifier,
                    progress_bar.termination,
                    self.dom0,
                ),
                callback=self.collect_result,
                error_callback=print,
            )
            if qube.klass == "AdminVM" and show_progress:
                # progress of AdminVM is continuation of different process,
                # so we want to skip 0 value at beginning
                progress_bar.progress_bars[qube.name].reset()

        progress_bar.pool.close()
        progress_bar.feeding()
        progress_bar.pool.join()
        progress_bar.close()
        self.log.info("Update Manager: Finished, collecting success info")

        # inform caller about requested cancel, even if all requested targets were updated
        if progress_bar.termination.value:
            self.ret_code = EXIT.SIGINT

        stats = list(progress_bar.statuses.values())
        if FinalStatus.CANCELLED in stats:
            self.ret_code = max(self.ret_code, EXIT.SIGINT)
        if FinalStatus.ERROR in stats:
            self.ret_code = max(self.ret_code, EXIT.ERR)
        if FinalStatus.UNKNOWN in stats:
            # communication with vm fails
            self.ret_code = max(self.ret_code, EXIT.ERR_QREXEX)

        if self.buffer:
            print(self.buffer)

        return self.ret_code, progress_bar.statuses

    def collect_result(self, result_tuple: Tuple[str, ProcessResult]) -> None:
        """
        Callback method to process `update_qube` output.
        """
        qube_name, result = result_tuple

        vm_code = result.code
        if result.code not in EXIT.VM_HANDLED:
            vm_code = EXIT.ERR_VM_UNHANDLED
        if vm_code == EXIT.OK_NO_UPDATES:
            # at this point, this code should be captured
            vm_code = EXIT.ERR_VM_UNHANDLED
        self.ret_code = max(self.ret_code, vm_code)

        if self.show_output:
            for line in result.out.split("\n"):
                self.print(FormatedLine(qube_name, "out", line))
            for line in result.err.split("\n"):
                self.print(FormatedLine(qube_name, "err", line))
        elif not self.quiet and self.no_progress:
            self.print(result.out)

    def print(self, *args: Any) -> None:
        if self.buffered:
            self.buffer += " ".join(map(str, args)) + "\n"
        else:
            print(*args, file=sys.stdout, flush=True)


class TerminalMultiBar:
    """
    Handles multiple progress bars in terminal.
    """

    def __init__(self) -> None:
        self.progresses: list[SimpleTerminalBar] = []

    def print(self) -> None:
        for progress in self.progresses:
            print(progress, file=sys.stderr, flush=True)


class SimpleTerminalBar:
    """
    Simple progress bar for terminal output. Could be used by TerminalMultiBar.
    """

    PARENT_MULTI_BAR = None
    DOWNLOAD_ONLY = False

    def __init__(
        self, total: int | float, position: int, desc: Optional[str]
    ) -> None:
        assert SimpleTerminalBar.PARENT_MULTI_BAR is not None
        assert position == len(SimpleTerminalBar.PARENT_MULTI_BAR.progresses)
        SimpleTerminalBar.PARENT_MULTI_BAR.progresses.append(self)
        self.desc: str = desc
        self.progress: float | None = 0.0
        self.total: int | float = total

    def __str__(self) -> str:
        info = None
        name, status = self.desc.split(" ", 1)
        status = status[1:-1]  # remove brackets
        if status in (
            FinalStatus.SUCCESS.value,
            FinalStatus.ERROR.value,
            FinalStatus.CANCELLED.value,
            FinalStatus.NO_UPDATES.value,
        ):
            if SimpleTerminalBar.DOWNLOAD_ONLY:
                return ""
            info = status.replace(" ", "_")
            status = "done"
        if status == Status.UPDATING.value:
            if self.progress is None:
                return ""
            info = str(self.progress)
        return f"{name} {status} {info}"

    def reset(self, total: int | float | None = None) -> None:
        if total is not None:
            self.total = total
        self.progress = None
        assert SimpleTerminalBar.PARENT_MULTI_BAR is not None
        SimpleTerminalBar.PARENT_MULTI_BAR.print()

    def update(self, progress: float) -> None:
        if self.progress is None:
            self.progress = 0.0
        self.progress += progress
        assert SimpleTerminalBar.PARENT_MULTI_BAR is not None
        SimpleTerminalBar.PARENT_MULTI_BAR.print()

    def set_description(self, desc: str) -> None:
        self.desc = desc
        assert SimpleTerminalBar.PARENT_MULTI_BAR is not None
        SimpleTerminalBar.PARENT_MULTI_BAR.print()

    def close(self) -> None:
        """Implementation of tqdm API"""

    @staticmethod
    def reinit_class(download_only: bool = False) -> None:
        SimpleTerminalBar.PARENT_MULTI_BAR = TerminalMultiBar()
        SimpleTerminalBar.DOWNLOAD_ONLY = download_only


Bar = Union[SimpleTerminalBar, tqdm]


class MultipleUpdateMultipleProgressBar:
    """
    Show update info for each qube in the terminal.
    """

    def __init__(
        self,
        dummy: bool,
        output: Callable[..., Bar],
        max_concurrency: int,
        printer: Optional[Callable],
    ) -> None:
        self.dummy = dummy

        self.manager = multiprocessing.Manager()
        self.termination = self.manager.Value("b", False)
        self.status_notifier = self.manager.Queue()

        # save original signal handler for SIGINT
        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        # set SIGINT handler to ignore, it will be inherited by processes
        # in pool
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.pool = multiprocessing.Pool(max_concurrency)
        # set SIGINT handler to graceful termination
        signal.signal(signal.SIGINT, self.signal_handler_during_feeding)

        self.progresses: dict[str, int | float] = {}
        self.progress_bars: dict[str, SimpleTerminalBar | tqdm] = {}
        self.statuses: dict[str, FinalStatus] = {}
        self.output_class = output
        self.print = printer

    def add_bar(self, qname: str) -> None:
        """
        Add progress bar for a qube given by the name.
        """
        if self.dummy:
            return

        self.progresses[qname] = 0
        self.progress_bars[qname] = self.output_class(
            total=100,
            position=len(self.progress_bars),
            desc=f"{qname} ({Status.PENDING.value})",
        )

    def feeding(self) -> None:
        """
        Consume info from queues and update progress bars.

        The loop is terminated when status `done` for all qubes is consumed.
        """
        if self.dummy:
            return

        left_to_finish = len(self.progresses)
        while left_to_finish:
            try:
                feed: Optional[StatusInfo | str] = self.status_notifier.get(
                    block=True
                )
                if feed is None:
                    continue
                if isinstance(feed, StatusInfo):
                    status_name = feed.status.value
                    if feed.status == Status.DONE:
                        left_to_finish -= 1
                        assert isinstance(feed.info, FinalStatus)
                        status_name = feed.info.value
                        self.statuses[feed.qname] = FinalStatus(status_name)
                    self.progress_bars[feed.qname].set_description(
                        f"{feed.qname} ({status_name})"
                    )
                    if feed.status == Status.UPDATING:
                        assert isinstance(feed.info, float)
                        self._update(feed.qname, feed.info)
                elif self.print is not None:
                    self.print(str(feed))
            except queue.Empty:
                pass

    def _update(self, qname: str, value: float) -> None:
        current = value
        progress = current - self.progresses[qname]
        self.progress_bars[qname].update(progress)
        self.progresses[qname] += progress

    def signal_handler_during_feeding(
        self, _sig: int, _frame: FrameType | None
    ) -> None:
        self.termination.value = True

    def close(self) -> None:
        """
        This method should be called after `multiprocessing.Pool.join`
        """
        signal.signal(signal.SIGINT, self.original_sigint_handler)

        if self.dummy:
            return

        for pbar in self.progress_bars.values():
            pbar.close()


def update_qube(
    qube: QubesVM,
    agent_args: argparse.Namespace,
    show_progress: bool,
    status_notifier: Any,
    termination: Any,
    dom0: bool,
) -> Tuple[str, ProcessResult]:
    """
    Create and run `UpdateAgentManager` for qube.

    :param qube: vm to update
    :param agent_args: args for agent entrypoint
    :param show_progress: if progress should be printed in real time
    :param status_notifier: an object to be fed with the progress data
    :param termination: signal to gracefully terminate subprocess
    :param dom0: whether to use qubes-dom0-update (do download&install)
                 or just update agent to install prepared updates
    :return:
    """
    if agent_args.display_name is not None:
        status_notifier = StatusNotifierWrapper(
            status_notifier, agent_args.display_name
        )

    if termination.value:
        status_notifier.put(StatusInfo.done(qube, FinalStatus.CANCELLED))
        return qube.name, ProcessResult(EXIT.SIGINT, "Canceled")

    try:
        runner = UpdateAgentManager(
            qube.app,
            qube,
            agent_args=agent_args,
            show_progress=show_progress,
            dom0=dom0,
        )
        result = runner.run_agent(
            agent_args=agent_args,
            status_notifier=status_notifier,
            termination=termination,
        )
    except Exception as exc:  # pylint: disable=broad-except
        status_notifier.put(StatusInfo.done(qube, FinalStatus.ERROR))
        return qube.name, ProcessResult(
            EXIT.ERR_VM_UNHANDLED, f"ERROR (exception {str(exc)})"
        )
    return qube.name, result


class UpdateAgentManager:
    """
    Send update agent files and run it in the qube.
    """

    AGENT_RELATIVE_DIR = "agent"
    ENTRYPOINT = AGENT_RELATIVE_DIR + "/entrypoint.py"
    LOGPATH = "/var/log/qubes"
    FORMAT_LOG = "%(asctime)s %(message)s"
    WORKDIR = "/run/qubes-update/"

    def __init__(
        self,
        app: QubesBase,
        qube: QubesVM,
        agent_args: argparse.Namespace,
        show_progress: bool,
        dom0: bool,
    ) -> None:
        self.qube = qube
        self.app = app
        self.dom0 = dom0

        (
            self.log,
            self.log_handler,
            _log_level,
            self.log_path,
            self.log_formatter,
        ) = init_logs(
            directory=self.LOGPATH,
            file=f"update-{qube.name}.log",
            format_=UpdateAgentManager.FORMAT_LOG,
            level=agent_args.log,
            truncate_file=False,
            qname=qube.name,
        )

        self.cleanup = not agent_args.no_cleanup
        self.show_progress = show_progress

    def run_agent(
        self,
        agent_args: argparse.Namespace,
        status_notifier: Any,
        termination: multiprocessing.managers.ValueProxy,
    ) -> ProcessResult:
        """
        Copy agent file to dest vm, run entrypoint, collect output and logs.
        """
        new_status_notifier = status_notifier
        if self.qube.klass == "AdminVM" and not self.dom0:
            # using UpdateVM to download updates
            new_status_notifier = StatusNotifierWrapper(status_notifier, "dom0")

        result = self._run_agent(agent_args, new_status_notifier, termination)

        self._log_output(result, agent_args.show_output)
        return result

    def _run_agent(
        self,
        agent_args: argparse.Namespace,
        status_notifier: Any,
        termination: multiprocessing.managers.ValueProxy,
    ) -> ProcessResult:
        self.log.info("Running update agent for %s", self.qube.name)
        dest_dir: Optional[str] = None
        src_dir: Optional[str] = None
        cleanup = False
        if self.qube.klass == "AdminVM":
            if self.dom0:
                entrypoint_cmd = ["sudo", "qubes-dom0-update", "-y"]
                if agent_args.just_print_progress or self.show_progress:
                    entrypoint_cmd.append("--just-print-progress")
                if agent_args.quiet:
                    # silent is equivalent to quiet for dom0-update
                    entrypoint_cmd.append("--silent")
                entrypoint: list[str] | str = entrypoint_cmd
            else:
                this_dir = os.path.dirname(os.path.realpath(__file__))
                entrypoint = join(this_dir, UpdateAgentManager.ENTRYPOINT)
        else:
            cleanup = self.cleanup
            dest_dir = UpdateAgentManager.WORKDIR
            entrypoint = os.path.join(dest_dir, UpdateAgentManager.ENTRYPOINT)
            this_dir = os.path.dirname(os.path.realpath(__file__))
            src_dir = join(this_dir, UpdateAgentManager.AGENT_RELATIVE_DIR)

        with QubeConnection(
            self.qube,
            dest_dir,
            cleanup,
            self.log,
            self.show_progress,
            status_notifier,
        ) as qconn:
            result = self._transfer_agent(qconn, src_dir)

            if termination.value:
                qconn.status = FinalStatus.CANCELLED
                return ProcessResult(EXIT.SIGINT, "", "Cancelled")

            result += self._run_entrypoint(qconn, entrypoint, agent_args)

            self._read_logs(qconn)

        return result

    def _transfer_agent(
        self, qconn: QubeConnection, src_dir: Optional[str]
    ) -> ProcessResult:
        result = ProcessResult()
        if self.qube.klass != "AdminVM":
            assert src_dir is not None
            self.log.info(
                "Transferring files to destination qube: %s", self.qube.name
            )
            result += qconn.transfer_agent(src_dir)
            if result:
                self.log.error("Qube communication error code: %i", result.code)
                return result
        return result

    def _run_entrypoint(
        self,
        qconn: QubeConnection,
        entrypoint: list[str] | str,
        agent_args: argparse.Namespace,
    ) -> ProcessResult:
        result = ProcessResult()
        self.log.info(
            "The agent is starting the task in qube: %s", self.qube.name
        )
        self.log.debug("%s", entrypoint)
        result += qconn.run_entrypoint(entrypoint, agent_args)
        if not result and qconn.status != FinalStatus.NO_UPDATES:
            qconn.status = FinalStatus.SUCCESS
        return result

    def _read_logs(self, qconn: QubeConnection) -> None:
        result_logs = qconn.read_logs()
        if result_logs:
            self.log.error(
                "Problem with collecting logs from %s, return code: %i",
                self.qube.name,
                result_logs.code,
            )
        # agent logs already have timestamp
        self.log_handler.setFormatter(logging.Formatter("%(message)s"))
        # critical -> always write agent logs
        for log_line in result_logs.out.split("\n"):
            if log_line:
                self.log.critical("%s", log_line)
        self.log_handler.setFormatter(self.log_formatter)

    def _log_output(self, result: ProcessResult, show_output: bool) -> None:
        output = result.out.split("\n") + result.err.split("\n")
        for line in output:
            self.log.debug("agent output: %s", line)
        self.log.info("agent exit code: %d", result.code)
        if not show_output or not output:
            result.out = (
                "OK"
                if result.code == EXIT.OK
                else f"ERROR (exit code {result.code}, details in {self.log_path})"
            )


class StatusNotifierWrapper:
    """
    Masks proxy VM with display name.
    """

    def __init__(self, status_notifier: Any, qube_name: str) -> None:
        self.status_notifier = status_notifier
        self.qube_name = qube_name

    def put(self, message: StatusInfo | FormatedLine) -> None:
        if isinstance(message, (StatusInfo, FormatedLine)):
            message.qname = self.qube_name
        self.status_notifier.put(message)
