# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022  Piotr Bartman <prbartman@invisiblethingslab.com>
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

import os
import signal
import sys
import queue
import logging
import multiprocessing
from os.path import join
from typing import Optional

from tqdm import tqdm

import qubesadmin.vm
import qubesadmin.exc
from .agent.source.status import StatusInfo, FinalStatus, Status
from .qube_connection import QubeConnection
from vmupdate.agent.source.log_congfig import init_logs


class UpdateManager:
    """
    Update multiple qubes simultaneously.
    """

    def __init__(self, qubes, args):
        self.qubes = qubes
        self.max_concurrency = args.max_concurrency
        self.show_output = args.show_output
        self.quiet = args.quiet
        self.no_progress = args.no_progress
        self.cleanup = not args.no_cleanup
        self.ret_code = 0

    def run(self, agent_args):
        """
        Run simultaneously `update_qube` for all qubes as separate processes.
        """
        if not self.qubes:
            return 0

        show_progress = not self.quiet and not self.no_progress
        SimpleTerminalBar.reinit_class()
        progress_output = SimpleTerminalBar \
            if agent_args.just_print_progress else tqdm
        progress_bar = MultipleUpdateMultipleProgressBar(
            dummy=not show_progress,
            output=progress_output,
            max_concurrency=self.max_concurrency
        )

        for qube in self.qubes:
            progress_bar.add_bar(qube.name)
            progress_bar.pool.apply_async(
                update_qube,
                (qube.name, agent_args, show_progress,
                 progress_bar.status_notifier, progress_bar.termination),
                callback=self.collect_result, error_callback=print
            )

        progress_bar.pool.close()
        progress_bar.feeding()
        progress_bar.pool.join()
        progress_bar.close()

        return self.ret_code

    def collect_result(self, result_tuple):
        """
        Callback method to process `update_qube` output.

        :param result_tuple: tuple(qube_name, ret_code, result)
        """
        qube_name, ret_code, result = result_tuple
        self.ret_code = max(self.ret_code, ret_code)
        if self.show_output and isinstance(result, list):
            sys.stdout.write(qube_name + ":")
            sys.stdout.write('\n'.join(['  ' + line for line in result]))
            sys.stdout.write('\n')
        elif not self.quiet and self.no_progress:
            print(qube_name + ": " + result)


class TerminalMultiBar:
    def __init__(self):
        self.progresses = []

    def print(self):
        for progress in self.progresses:
            print(progress, file=sys.stderr, flush=True)


class SimpleTerminalBar:
    PARENT_MULTI_BAR = None

    def __init__(self, total, position, desc):
        assert position == len(SimpleTerminalBar.PARENT_MULTI_BAR.progresses)
        SimpleTerminalBar.PARENT_MULTI_BAR.progresses.append(self)
        self.desc = desc
        self.progress = 0
        self.total = total

    def __str__(self):
        info = None
        name, status = self.desc.split(' ', 1)
        status = status[1:-1]  # remove brackets
        if status in (FinalStatus.SUCCESS.value,
                      FinalStatus.ERROR.value,
                      FinalStatus.CANCELLED.value,
                      FinalStatus.NO_UPDATES.value):
            info = status.replace(" ", "_")
            status = "done"
        if status == Status.PENDING.value:
            info = self.progress
        return f"{name} {status} {info}"

    def update(self, progress):
        self.progress += progress
        SimpleTerminalBar.PARENT_MULTI_BAR.print()

    def set_description(self, desc: str):
        self.desc = desc
        SimpleTerminalBar.PARENT_MULTI_BAR.print()

    def close(self):
        pass

    @staticmethod
    def reinit_class():
        SimpleTerminalBar.PARENT_MULTI_BAR = TerminalMultiBar()


class MultipleUpdateMultipleProgressBar:
    """
    Show update info for each qube in the terminal.
    """

    def __init__(self, dummy, output, max_concurrency):
        self.dummy = dummy

        self.manager = multiprocessing.Manager()
        self.termination = self.manager.Value('b', False)
        self.status_notifier = self.manager.Queue()

        # save original signal handler for SIGINT
        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        # set SIGINT handler to ignore, it will be inherited by processes
        # in pool
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.pool = multiprocessing.Pool(max_concurrency)
        # set SIGINT handler to gracefully termination
        signal.signal(signal.SIGINT, self.signal_handler_during_feeding)

        self.progresses = {}
        self.progress_bars = {}
        self.output_class = output

    def add_bar(self, qname: str):
        """
        Add progress bar for a qube given by the name.
        """
        if self.dummy:
            return

        self.progresses[qname] = 0
        self.progress_bars[qname] = self.output_class(
            total=100, position=len(self.progress_bars),
            desc=f"{qname} ({Status.WAIT.value})"
        )

    def feeding(self):
        """
        Consume info from queues and update progress bars.

        The loop is terminated when status `done` for all qubes is consumed.
        """
        if self.dummy:
            return

        left_to_finish = len(self.progresses)
        while left_to_finish:
            try:
                feed: Optional[StatusInfo] = \
                    self.status_notifier.get(block=True)
                if feed is None:
                    continue
                status_name = feed.status.value
                if feed.status == Status.DONE:
                    left_to_finish -= 1
                    status_name = feed.info.value
                self.progress_bars[feed.qname].set_description(
                    f"{feed.qname} ({status_name})")
                if feed.status == Status.PENDING:
                    self._update(feed.qname, feed.info)
            except queue.Empty:
                pass

    def _update(self, qname: str, value: float):
        current = value
        progress = current - self.progresses[qname]
        self.progress_bars[qname].update(progress)
        self.progresses[qname] += progress

    def signal_handler_during_feeding(self, _sig, _frame):
        print('Waiting for running updates to finish...')
        self.termination.value = True

    def close(self):
        """
        This method should be called after `multiprocessing.Pool.join`
        """
        signal.signal(signal.SIGINT, self.original_sigint_handler)

        if self.dummy:
            return

        for pbar in self.progress_bars.values():
            pbar.close()


def update_qube(
        qname, agent_args, show_progress, status_notifier, termination):
    """
    Create and run `UpdateAgentManager` for qube.

    :param qname: name of qube
    :param agent_args: args for agent entrypoint
    :param show_progress: if progress should be printed in real time
    :param status_notifier: object to be fed with the progress data
    :param termination: signal to gracefully terminate subprocess
    :return:
    """
    app = qubesadmin.Qubes()
    try:
        qube = app.domains[qname]
    except KeyError:
        return qname, 2, "ERROR (qube not found)"

    if termination.value:
        status_notifier.put(StatusInfo.done(qube, FinalStatus.CANCELLED))
        return qname, 130, "Canceled"
    status_notifier.put(StatusInfo.pending(qube, 0))

    try:
        runner = UpdateAgentManager(
            app,
            qube,
            agent_args=agent_args,
            show_progress=show_progress
        )
        ret_code, result = runner.run_agent(
            agent_args=agent_args,
            status_notifier=status_notifier,
            termination=termination
        )
    except Exception as exc:  # pylint: disable=broad-except
        return qname, 1, f"ERROR (exception {str(exc)})"
    return qube.name, ret_code, result


class UpdateAgentManager:
    """
    Send update agent files and run it in the qube.
    """
    AGENT_RELATIVE_DIR = "agent"
    ENTRYPOINT = AGENT_RELATIVE_DIR + "/entrypoint.py"
    LOGPATH = '/var/log/qubes'
    FORMAT_LOG = '%(asctime)s %(message)s'
    WORKDIR = "/run/qubes-update/"

    def __init__(
            self, app, qube, agent_args, show_progress):
        self.qube = qube
        self.app = app

        (self.log, self.log_handler, log_level,
         self.log_path, self.log_formatter) = init_logs(
            directory=UpdateAgentManager.LOGPATH,
            file=f'update-{qube.name}.log',
            format_=UpdateAgentManager.FORMAT_LOG,
            level=agent_args.log,
            truncate_file=False,
        )

        self.cleanup = not agent_args.no_cleanup
        self.show_progress = show_progress

    def run_agent(self, agent_args, status_notifier, termination):
        """
        Copy agent file to dest vm, run entrypoint, collect output and logs.
        """
        ret_code, output = self._run_agent(
            agent_args, status_notifier, termination)
        for line in output:
            self.log.debug('agent output: %s', line)
        self.log.info('agent exit code: %d', ret_code)
        if agent_args.show_output and output:
            return_data = output
        else:
            return_data = "OK" if ret_code == 0 else \
                f"ERROR (exit code {ret_code}, details in {self.log_path})"
        return ret_code, return_data

    def _run_agent(self, agent_args, status_notifier, termination):
        self.log.info('Running update agent for %s', self.qube.name)
        dest_dir = UpdateAgentManager.WORKDIR
        dest_agent = os.path.join(dest_dir, UpdateAgentManager.ENTRYPOINT)
        this_dir = os.path.dirname(os.path.realpath(__file__))
        src_dir = join(this_dir, UpdateAgentManager.AGENT_RELATIVE_DIR)

        with QubeConnection(
                self.qube,
                dest_dir,
                self.cleanup,
                self.log,
                self.show_progress,
                status_notifier
        ) as qconn:
            self.log.info(
                "Transferring files to destination qube: %s", self.qube.name)
            ret_code, output = qconn.transfer_agent(src_dir)
            if ret_code:
                self.log.error('Qube communication error code: %i', ret_code)
                qconn.status_notifier.put(
                    StatusInfo.done(self.qube, FinalStatus.ERROR))
                qconn.status_notified = True
                return ret_code, output

            if termination.value:
                status_notifier.put(
                    StatusInfo.done(self.qube, FinalStatus.CANCELLED))
                qconn.status_notified = True
                return 130, "Cancelled"

            self.log.info(
                "The agent is starting the task in qube: %s", self.qube.name)
            ret_code_, output = qconn.run_entrypoint(
                dest_agent, agent_args)
            if ret_code:
                qconn.status_notifier.put(
                    StatusInfo.done(self.qube, FinalStatus.ERROR))
                qconn.status_notified = True
            ret_code = max(ret_code, ret_code_)

            ret_code_logs, logs = qconn.read_logs()
            if ret_code_logs:
                self.log.error(
                    "Problem with collecting logs from %s, return code: %i",
                    self.qube.name, ret_code_logs)
            # agent logs already have timestamp
            self.log_handler.setFormatter(logging.Formatter('%(message)s'))
            # critical -> always write agent logs
            for log_line in logs:
                self.log.critical("%s", log_line)
            self.log_handler.setFormatter(self.log_formatter)

        return ret_code, output
