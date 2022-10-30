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
import sys
import queue
import logging
import multiprocessing
from os.path import join
from tqdm import tqdm

import qubesadmin.vm
import qubesadmin.exc
from .qube_connection import QubeConnection


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

        pool = multiprocessing.Pool(self.max_concurrency)
        show_progress = not self.quiet and not self.no_progress
        progress_bar = MultipleUpdateMultipleProgressBar(
            dummy=not show_progress)

        for qube in self.qubes:
            progress_bar.add_bar(qube.name)
            pool.apply_async(
                update_qube,
                (qube.name, agent_args, show_progress, progress_bar.queue),
                callback=self.collect_result, error_callback=print
            )

        pool.close()
        progress_bar.feeding()
        pool.join()
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
            sys.stdout.write(qube_name + ":\n")
            sys.stdout.write('\n'.join(['  ' + line for line in result]))
            sys.stdout.write('\n')
        elif not self.quiet and self.no_progress:
            print(qube_name + ": " + result)


class MultipleUpdateMultipleProgressBar:
    """
    Show update info for each qube in the terminal.
    """

    def __init__(self, dummy):
        self.dummy = dummy
        if self.dummy:
            self.manager = None
            self.queue = None
        else:
            self.manager = multiprocessing.Manager()
            self.queue = self.manager.Queue()
        self.progresses = {}
        self.progress_bars = {}

    def add_bar(self, qname: str):
        """
        Add progress bar for a qube given by the name.
        """
        if self.dummy:
            return

        self.progresses[qname] = 0
        self.progress_bars[qname] = tqdm(
            total=100, position=len(self.progress_bars), desc=qname
        )

    def feeding(self):
        """
        Consume info from queues and update progress bars.

        The loop is terminated when `QubeConnection.TERMINATOR` for all queues
        is consumed.
        """
        if self.dummy:
            return

        left_to_finish = len(self.progresses)
        while left_to_finish:
            try:
                feed = self.queue.get(block=True)
                if len(feed) == 1:
                    left_to_finish -= 1
                    self._update(feed[0], 100.)
                elif len(feed) == 2:
                    self._update(*feed)
                else:
                    raise RuntimeError(
                        f"Unexpected number of elements in queue item: {feed}")
            except queue.Empty:
                pass

    def _update(self, qname: str, value: float):
        current = value
        progress = current - self.progresses[qname]
        self.progress_bars[qname].update(progress)
        self.progresses[qname] += progress

    def close(self):
        """
        This method should be called after `multiprocessing.Pool.join`
        """
        if self.dummy:
            return

        for pbar in self.progress_bars.values():
            pbar.close()


def update_qube(qname, agent_args, show_progress, progress_collector):
    """
    Create and run `UpdateAgentManager` for qube.

    :param qname: name of qube
    :param agent_args: args for agent entrypoint
    :param show_progress: if progress should be printed in real time
    :param progress_collector: object to be fed with the progress data
    :return:
    """
    app = qubesadmin.Qubes()
    try:
        qube = app.domains[qname]
    except KeyError:
        return qname, 2, "ERROR (qube not found)"
    try:
        runner = UpdateAgentManager(
            app,
            qube,
            agent_args=agent_args,
            show_progress=show_progress
        )
        ret_code, result = runner.run_agent(
            agent_args=agent_args,
            progress_collector=progress_collector
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
    FORMAT_LOG = '%(asctime)s %(message)s'
    LOGPATH = '/var/log/qubes'
    WORKDIR = "/run/qubes-update/"

    def __init__(
            self, app, qube, agent_args, show_progress):
        self.qube = qube
        self.app = app
        self.log = logging.getLogger('vm-update.qube.' + qube.name)
        self.log_path = os.path.join(
            UpdateAgentManager.LOGPATH, f'update-{qube.name}.log')
        self.logfile_handler = logging.FileHandler(
            self.log_path,
            encoding='utf-8')
        self.log_formatter = logging.Formatter(UpdateAgentManager.FORMAT_LOG)
        self.logfile_handler.setFormatter(self.log_formatter)
        self.log.addHandler(self.logfile_handler)
        self.log.setLevel(agent_args.log)
        self.log.propagate = False
        self.cleanup = not agent_args.no_cleanup
        self.show_progress = show_progress

    def run_agent(self, agent_args, progress_collector):
        """
        Copy agent file to dest vm, run entrypoint, collect output and logs.
        """
        ret_code, output = self._run_agent(agent_args, progress_collector)
        for line in output:
            self.log.debug('agent output: %s', line)
        self.log.info('agent exit code: %d', ret_code)
        if agent_args.show_output and output:
            return_data = output
        else:
            return_data = "OK" if ret_code == 0 else \
                f"ERROR (exit code {ret_code}, details in {self.log_path})"
        return ret_code, return_data

    def _run_agent(self, agent_args, progress_collector):
        self.log.info('Running update agent for %s', self.qube.name)
        dest_dir = UpdateAgentManager.WORKDIR
        dest_agent = os.path.join(dest_dir, UpdateAgentManager.ENTRYPOINT)
        this_dir = os.path.dirname(os.path.realpath(__file__))
        src_dir = join(this_dir, UpdateAgentManager.AGENT_RELATIVE_DIR)

        with QubeConnection(
                self.qube, dest_dir, self.cleanup, self.log, self.show_progress
        ) as qconn:
            self.log.info(
                "Transferring files to destination qube: %s", self.qube.name)
            ret_code, output = qconn.transfer_agent(src_dir)
            if ret_code:
                self.log.error('Qube communication error code: %i', ret_code)
                return ret_code, output

            self.log.info(
                "The agent is starting the task in qube: %s", self.qube.name)
            ret_code_, output = qconn.run_entrypoint(
                dest_agent, agent_args, progress_collector)
            ret_code = max(ret_code, ret_code_)

            ret_code_logs, logs = qconn.read_logs()
            if ret_code_logs:
                self.log.error(
                    "Problem with collecting logs from %s, return code: %i",
                    self.qube.name, ret_code_logs)
            # agent logs already have timestamp
            self.logfile_handler.setFormatter(logging.Formatter('%(message)s'))
            # critical -> always write agent logs
            for log_line in logs:
                self.log.critical("%s", log_line)
            self.logfile_handler.setFormatter(self.log_formatter)

        return ret_code, output
