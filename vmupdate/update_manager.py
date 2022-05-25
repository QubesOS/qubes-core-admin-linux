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
import logging
import multiprocessing
from os.path import join

import qubesadmin.vm
import qubesadmin.exc
from .qube_connection import QubeConnection


class UpdateManager:
    """
    Update multiple qubes simultaneously.
    """

    def __init__(self, qubes, max_concurrency, show_output, quiet, cleanup):
        self.qubes = qubes
        self.max_concurrency = max_concurrency
        self.show_output = show_output
        self.quiet = quiet
        self.cleanup = cleanup
        self.exit_code = 0

    def run(self, agent_args):
        """
        Run simultaneously `update_qube` for all qubes as separate processes.
        """
        pool = multiprocessing.Pool(self.max_concurrency)
        show_progress = not self.quiet and (len(self.qubes) == 1
                                            or self.max_concurrency == 1)
        for qube in self.qubes:
            pool.apply_async(
                update_qube,
                (qube.name, self.show_output, self.cleanup, self.quiet,
                 agent_args, show_progress),
                callback=self.collect_result
            )
        pool.close()
        pool.join()
        return self.exit_code

    def collect_result(self, result_tuple):
        """
        Callback method to process `update_qube` output.

        :param result_tuple: tuple(qube_name, exit_code, result)
        """
        qube_name, exit_code, result = result_tuple
        self.exit_code = max(self.exit_code, exit_code)
        if self.show_output and isinstance(result, list):
            sys.stdout.write(qube_name + ":\n")
            sys.stdout.write('\n'.join(['  ' + line for line in result]))
            sys.stdout.write('\n')
        elif not self.quiet:
            print(qube_name + ": " + result)


def update_qube(qname, show_output, quiet, cleanup, agent_args, show_progress):
    """
    Create and run `UpdateAgentManager` for qube.

    :param qname: name of qube
    :param show_output: flag, if true print full output
    :param quiet: flag, if true no output will be produced
    :param cleanup: flag, if true updater files will be removed from the qube
    :param agent_args: args for agent entrypoint
    :param show_progress: if progress should be printed in real time
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
            quiet=quiet,
            cleanup=cleanup,
            loglevel=agent_args.log,
            show_progress=show_progress
        )
        exit_code, result = runner.run_agent(
            return_output=show_output, agent_args=agent_args)
    except Exception as e:  # pylint: disable=broad-except
        return qname, 1, "ERROR (exception {})".format(str(e))
    return qube.name, exit_code, result


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
            self, app, qube, quiet, cleanup, loglevel, show_progress):
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
        self.log.setLevel(loglevel)
        self.log.propagate = False
        self.quiet = quiet
        self.cleanup = cleanup
        self.show_progress = show_progress

    def run_agent(self, return_output, agent_args):
        self.log.info('Running update agent for {}'.format(self.qube.name))
        dest_dir = UpdateAgentManager.WORKDIR
        dest_agent = os.path.join(dest_dir, UpdateAgentManager.ENTRYPOINT)
        this_dir = os.path.dirname(os.path.realpath(__file__))
        src_dir = join(this_dir, UpdateAgentManager.AGENT_RELATIVE_DIR)

        with QubeConnection(
                self.qube, dest_dir, self.cleanup, self.log, self.show_progress
        ) as qc:
            self.log.info("Transferring files to destination qube: {}".format(
                self.qube.name))
            exit_code, output = qc.transfer_agent(src_dir)
            for line in output:
                self.log.debug('connection output: %s', line)
            if exit_code:
                self.log.error('Qube communication error code:', exit_code)

            self.log.info("The agent is starting the task in qube: {}".format(
                self.qube.name))
            exit_code_, output = qc.run_entrypoint(dest_agent, agent_args)
            exit_code = max(exit_code, exit_code_)

            exit_code_logs, logs = qc.read_logs()
            self.logfile_handler.setFormatter(logging.Formatter('%(message)s'))
            # critical -> always write agent logs
            self.log.critical('\n'.join(logs))
            self.logfile_handler.setFormatter(self.log_formatter)

            for line in output:
                self.log.debug('agent output: %s', line)
            self.log.info('agent exit code: %d', exit_code)

            if return_output and output:
                return_data = output
            else:
                return_data = "OK" if exit_code == 0 else \
                    "ERROR (exit code {}, details in {})".format(
                        exit_code, self.log_path)

        return exit_code, return_data
