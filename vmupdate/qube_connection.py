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
import os
import shutil
import signal
import tempfile
import concurrent.futures
from os.path import join
from subprocess import CalledProcessError
from typing import List

import qubesadmin
from vmupdate.agent.source.args import AgentArgs
from vmupdate.agent.source.log_congfig import LOGPATH, LOG_FILE
from vmupdate.agent.source.status import StatusInfo, FinalStatus
from vmupdate.agent.source.common.process_result import ProcessResult


class QubeConnection:
    """
    Run scripts in the qube.

    1. Initialize the state of connection.
    2. Transfer files to a new directory, start the qube if not running.
    3. Run an entrypoint script, return the output.
    4. On close, remove the created directory,
       stop the qube if it was started by this connection.
    """

    def __init__(
            self,
            qube,
            dest_dir,
            cleanup,
            logger,
            show_progress,
            status_notifier
    ):
        self.qube = qube
        self.dest_dir = dest_dir
        self.cleanup = cleanup
        self.logger = logger
        self.show_progress = show_progress
        self.status_notifier = status_notifier
        self.status = FinalStatus.ERROR
        self._initially_running = None
        self.__connected = False

    def __enter__(self):
        self._initially_running = self.qube.is_running()
        self.__connected = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Do cleanup.

        1. If a progress collector is provided, send a signal that the update
           has been completed.
        2. Delete the uploaded files from the updated qube.
        3. Shut down qube if it wasn't running before the update.
        """
        self.status_notifier.put(StatusInfo.done(self.qube, self.status))

        if self.cleanup:
            self.logger.info('Remove %s', self.dest_dir)
            try:
                self._run_shell_command_in_qube(
                    self.qube, ['rm', '-r', self.dest_dir])
            except Exception as err:
                self.logger.error('Cannot remove %s, because of error: %s',
                                  self.dest_dir, str(err))

        if self.qube.is_running() and not self._initially_running:
            self.logger.info('Shutdown %s', self.qube.name)
            self.qube.shutdown()

        self.__connected = False

    def transfer_agent(self, src_dir: str) -> ProcessResult:
        """
        Copy a directory content to the workdir in the qube.

        :param src_dir: str: path to local (dom0) directory
        """
        assert self.__connected  # open the connection first

        arch_format = ".tar.gz"

        arch_dir = tempfile.mkdtemp()
        root_dir = os.path.dirname(src_dir)
        base_dir = os.path.basename(src_dir.strip(os.sep))
        src_arch = join(arch_dir, base_dir + arch_format)
        dest_arch = join(self.dest_dir, base_dir + arch_format)
        shutil.make_archive(base_name=join(arch_dir, base_dir),
                            format='gztar', root_dir=root_dir,
                            base_dir=base_dir)

        command = ['mkdir', '-p', self.dest_dir]
        result = self._run_shell_command_in_qube(self.qube, command)
        if result:
            return result

        result += self._copy_file_from_dom0(src_arch, dest_arch)
        if result:
            return result

        command = ["tar", "-xzf", dest_arch, "-C", self.dest_dir]
        result += self._run_shell_command_in_qube(self.qube, command)
        return result

    def _copy_file_from_dom0(self, src, dest) -> ProcessResult:
        write_dest = ["cat", ">", dest]
        command = " ".join(write_dest)
        self.logger.debug("run command: %s < %s", command, src)
        try:
            with open(src, 'rb') as file:
                untrusted_stdout_and_stderr = self.qube.run(
                    command, user='root', input=file.read()
                )
                result = ProcessResult.from_untrusted_out_err(
                    *untrusted_stdout_and_stderr)
            if result.code:
                raise OSError(f"Command returns code: {result.code}")
        except OSError as exc:
            result = ProcessResult(1, str(exc))

        return result

    def run_entrypoint(
            self, entrypoint_path: str, agent_args
    ) -> ProcessResult:
        """
        Run a script in the qube.

        :param entrypoint_path: path to the entrypoint.py in the qube
        :param agent_args: args for agent entrypoint
        :return: return code and output of the script
        """
        # make sure entrypoint is executable
        command = ['chmod', 'u+x', entrypoint_path]
        result = self._run_shell_command_in_qube(self.qube, command)

        # run entrypoint
        command = [entrypoint_path, *AgentArgs.to_cli_args(agent_args)]
        result += self._run_shell_command_in_qube(
            self.qube, command, show=self.show_progress)

        return result

    def read_logs(self) -> ProcessResult:
        """
        Read vm logs file.
        """
        command = ['cat',
                   str(join(LOGPATH, LOG_FILE))]
        result = self._run_shell_command_in_qube(self.qube, command)
        return result

    def _run_shell_command_in_qube(
            self, target, command: List[str], show: bool = False
    ) -> ProcessResult:
        self.logger.debug("run command in %s: %s",
                          target.name, " ".join(command))
        if not show:
            return self._run_command_and_wait_for_output(target, command)
        else:
            return self._run_command_and_actively_report_progress(
                    target, command)

    def _run_command_and_wait_for_output(
            self, target, command: List[str]
    ) -> ProcessResult:
        try:
            untrusted_stdout_and_stderr = target.run_with_args(
                *command, user='root'
            )
            result = ProcessResult.from_untrusted_out_err(
                *untrusted_stdout_and_stderr)
        except CalledProcessError as err:
            if err.returncode == 100:
                self.status = FinalStatus.NO_UPDATES
                ret_code = 0
            else:
                self.logger.error(str(err))
                ret_code = err.returncode
            result = ProcessResult.from_untrusted_out_err(
                err.output, err.output)
            result.code = ret_code
        except Exception as err:
            result = ProcessResult(1, "", str(err))
        return result

    def _run_command_and_actively_report_progress(
            self, target, command: List[str]
    ) -> ProcessResult:
        proc = target.run_service(
            'qubes.VMExec+' + qubesadmin.utils.encode_for_vmexec(command),
            user='root',
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
        )

        self.logger.debug("Fetching agent process stdout/stderr.")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit the methods to the executor
            future_err = executor.submit(self._collect_stderr, proc=proc)
            future_out = executor.submit(self._collect_stdout, proc=proc)

            result = ProcessResult.from_untrusted_out_err(
                future_out.result(), future_err.result())

        result.code = proc.returncode
        self.logger.debug("Agent process finished.")
        if result.code == 100:
            self.status = FinalStatus.NO_UPDATES
            result.code = 0
        return result

    def _collect_stderr(self, proc) -> bytes:
        stderr = b""
        progress_finished = False
        for untrusted_line in iter(proc.stderr.readline, None):
            if untrusted_line is not None:
                if not progress_finished:
                    line = ProcessResult.sanitize_output(untrusted_line)
                    try:
                        progress = float(line)
                    except ValueError:
                        stderr += untrusted_line
                        continue

                    if progress == 100.:
                        progress_finished = True
                    self.status_notifier.put(
                        StatusInfo.updating(self.qube, progress))
                else:
                    stderr += untrusted_line + b'\n'
            elif proc.poll() is not None:
                break

        proc.stderr.close()
        self.logger.debug("Agent stderr closed.")

        return stderr

    def _collect_stdout(self, proc) -> bytes:
        stdout = b""

        for untrusted_line in iter(proc.stdout.readline, None):
            if untrusted_line is not None:
                stdout += untrusted_line
            elif proc.poll() is not None:
                break

        proc.stdout.close()
        self.logger.debug("Agent stdout closed.")

        return stdout
