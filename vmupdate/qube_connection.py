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
import tempfile
from os.path import join
from subprocess import CalledProcessError
from .agent.source.common import package_manager

from vmupdate.agent.source.args import AgentArgs


class QubeConnection:
    """
    Run scripts in the qube.

    1. Initialize the state of connection.
    2. Transfer files to a new directory, start the qube if not running.
    3. Run an entrypoint script, return the output.
    4. On close, remove the created directory,
       stop the qube if it was started by this connection.
    """

    def __init__(self, qube, dest_dir, cleanup, logger, show_progress):
        self.qube = qube
        self.dest_dir = dest_dir
        self.cleanup = cleanup
        self.logger = logger
        self.show_progress = show_progress
        self._initially_running = None
        self.__connected = False

    def __enter__(self):
        self._initially_running = self.qube.is_running()
        self.__connected = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cleanup:
            self.logger.info('Remove %s', self.dest_dir)
            self._run_shell_command_in_qube(
                self.qube, ['rm', '-r', self.dest_dir])

        if self.qube.is_running() and not self._initially_running:
            self.logger.info('Shutdown %s', self.qube.name)
            self.qube.shutdown()

        self.__connected = False

    def transfer_agent(self, src_dir):
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

        run_cmd = f"qvm-run --user=root --pass-io {self.qube.name} "

        command = ['mkdir', '-p', self.dest_dir]
        exit_code, output = self._run_shell_command_in_qube(
            self.qube, command)

        command = f"cat {src_arch} | " + \
                  run_cmd + f"'cat > {dest_arch}'"
        self._run_shell_command_in_dom0(command)

        command = ["tar", "-xzf", dest_arch, "-C", self.dest_dir]
        exit_code_, output_ = self._run_shell_command_in_qube(
            self.qube, command)

        exit_code = max(exit_code, exit_code_)
        output += output_

        return exit_code, output

    def _run_shell_command_in_dom0(self, command: str):
        self.logger.debug("run command: %s", command)
        os.system(command)

    def run_entrypoint(self, entrypoint_path, agent_args):
        """
        Run a script in the qube.

        :param entrypoint_path: str: path to the entrypoint.py in the qube
        :param agent_args: args for agent entrypoint
        :return: Tuple[int, str]: return code and output of the script
        """
        # make sure entrypoint is executable
        command = ['chmod', 'u+x', entrypoint_path]
        exit_code, output = self._run_shell_command_in_qube(self.qube, command)

        # run entrypoint
        command = [entrypoint_path, *AgentArgs.to_cli_args(agent_args)]
        exit_code_, output_ = self._run_shell_command_in_qube(
            self.qube, command, show=self.show_progress
        )
        exit_code = max(exit_code, exit_code_)
        output += output_

        return exit_code, output

    def read_logs(self):
        """
        Read vm logs file.
        """
        command = ['cat',
                   str(join(package_manager.LOGPATH, package_manager.LOG_FILE))]
        exit_code, output = self._run_shell_command_in_qube(self.qube, command)
        return exit_code, output

    def _run_shell_command_in_qube(self, target, command, show=False):
        self.logger.debug("run command in %s: %s",
                          target.name, " ".join(command))
        if not show:
            try:
                untrusted_stdout_and_stderr = target.run_with_args(*command,
                                                                   user='root')
                returncode = 0
            except CalledProcessError as e:
                self.logger.error(str(e))
                returncode = e.returncode
                untrusted_stdout_and_stderr = (b"", b"")
        else:
            p = target.run_service('qubes.VMRootShell', user='root')
            p.stdin.write((" ".join(command) + "\n").encode())
            p.stdin.close()
            stdout = b""
            stderr = b""

            progress_finished = False
            for untrusted_line in iter(p.stdout.readline, ''):
                if untrusted_line:
                    if not progress_finished:
                        line = QubeConnection._string_sanitization(
                            untrusted_line.decode().rstrip())
                        if line.strip() == "100.00%":
                            progress_finished = True
                        print(self.qube.name + ":", line, end="\r")
                    else:
                        stdout += untrusted_line
                else:
                    # erase previous output
                    print(self.qube.name + ":..." + 8 * " ", end="\r")
                    break
            p.stdout.close()

            for untrusted_line in iter(p.stderr.readline, ''):
                if untrusted_line:
                    stderr += untrusted_line
                else:
                    break
            p.stderr.close()

            p.wait()
            untrusted_stdout_and_stderr = (stdout, stderr)
            returncode = p.returncode

        return returncode, QubeConnection._collect_output(
            *untrusted_stdout_and_stderr)

    @staticmethod
    def _collect_output(untrusted_stdout, untrusted_stderr):
        untrusted_stdout = untrusted_stdout.decode('ascii', errors='ignore') + \
                           untrusted_stderr.decode('ascii', errors='ignore')

        # removing control characters
        stdout_lines = [QubeConnection._string_sanitization(line)
                        for line in untrusted_stdout.splitlines()]
        return stdout_lines

    @staticmethod
    def _string_sanitization(line: str) -> str:
        """
        Removing control characters
        """
        return ''.join([c for c in line if 0x20 <= ord(c) <= 0x7e])
