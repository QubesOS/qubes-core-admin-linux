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
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, List, Tuple


LOGPATH = '/var/log/qubes/qubes-update'
FORMAT_LOG = '%(asctime)s [Agent] %(message)s'
LOG_FILE = 'update-agent.log'
Path(LOGPATH).mkdir(parents=True, exist_ok=True)
formatter_log = logging.Formatter(FORMAT_LOG)


class PackageManager:
    def __init__(self, loglevel):
        self.package_manager: Optional[str] = None
        self.log = logging.getLogger('vm-update.agent.PackageManager')
        try:
            # if loglevel is unknown just use `DEBUG`
            self.log.setLevel(loglevel)
        except ValueError:
            self.log.setLevel("DEBUG")
        self.log_path = os.path.join(LOGPATH, LOG_FILE)
        with open(self.log_path, "w"):
            # We want temporary logs here, so we truncate log file
            # persistent logs are at dom0
            pass
        handler_log = logging.FileHandler(self.log_path, encoding='utf-8')
        handler_log.setFormatter(formatter_log)
        self.log.addHandler(handler_log)
        self.log.propagate = False

    def upgrade(
            self,
            refresh: bool,
            hard_fail: bool,
            remove_obsolete: bool,
            requirements: Optional[Dict[str, str]] = None,
            print_streams: bool = False
    ):
        """
        Upgrade packages using system package manager.

        :param refresh: refresh available packages first
        :param hard_fail: if refresh or installing requirements fails,
                          stop and fail
        :param remove_obsolete: remove obsolete packages
        :param requirements: packages versions required before full upgrade
        :param print_streams: dump captured output to std streams
        :return: return code
        """
        return_code, stdout, stderr = self._upgrade(
            refresh, hard_fail, remove_obsolete, requirements)
        if print_streams:
            print(stdout, flush=True)
            print(stderr, file=sys.stderr, flush=True)
        return return_code

    def _upgrade(
            self,
            refresh: bool,
            hard_fail: bool,
            remove_obsolete: bool,
            requirements: Optional[Dict[str, str]] = None
    ):
        stdout = ""
        stderr = ""
        if refresh:
            ret_code, stdout_, stderr_ = self.refresh()
            self.log_output("refresh", stdout_, stderr_, bool(ret_code))
            stdout += stdout_
            stderr += stderr_
            if ret_code != 0:
                self.log.warning("Refreshing failed.")
                if hard_fail:
                    self.log.error("Exiting due to a refresh error. "
                                   "Use --force-upgrade to upgrade anyway.")
                    return ret_code, stdout, stderr

        curr_pkg = self.get_packages()

        if requirements:
            ret_code, stdout_, stderr_ = self.install_requirements(
                requirements, curr_pkg)
            self.log_output(
                "install requirements", stdout_, stderr_, bool(ret_code))
            stdout += stdout_
            stderr += stderr_
            if ret_code != 0:
                self.log.warning("Installing requirements failed.")
                if hard_fail:
                    self.log.error("Exiting due to a packages install error. "
                                   "Use --force-upgrade to upgrade anyway.")
                    return ret_code, stdout, stderr

        ret_code, stdout_, stderr_ = self.upgrade_internal(remove_obsolete)
        self.log_output("upgrade", stdout_, stderr_, bool(ret_code))
        stdout += stdout_
        stderr += stderr_

        new_pkg = self.get_packages()

        changes = PackageManager.compare_packages(old=curr_pkg, new=new_pkg)
        self.log_changes(changes)

        return ret_code, stdout, stderr

    def log_output(self, title, stdout, stderr, log_as_error=False):
        if stdout:
            out_lines = stdout.split("\n")
            log = self.log.error if log_as_error else self.log.debug
            for out_line in out_lines:
                log("%s out: %s", title, out_line)
        if stderr:
            err_lines = stdout.split("\n")
            log = self.log.error if log_as_error else self.log.debug
            for err_line in err_lines:
                log("%s err: %s", title, err_line)

    def install_requirements(
            self,
            requirements: Optional[Dict[str, str]],
            curr_pkg: Dict[str, List[str]]
    ) -> Tuple[int, str, str]:
        if requirements is None:
            requirements = {}

        exit_code = 0
        out = ""
        err = ""
        to_install = []  # install latest (ignore version)
        to_upgrade = {}
        for pkg, version in requirements.items():
            if pkg not in curr_pkg:
                to_install.append(pkg)
            else:
                for v in curr_pkg[pkg]:
                    if version < v:
                        break
                else:
                    to_upgrade[pkg] = version
        if to_install:
            cmd = [self.package_manager,
                   "-q",
                   "-y",
                   "install",
                   *to_install]
            ret_code, stdout, stderr = self.run_cmd(cmd)
            exit_code = max(exit_code, ret_code)
            out += stdout
            err += stderr

        if to_upgrade:
            cmd = [self.package_manager,
                   "-q",
                   "-y",
                   *self.get_action(remove_obsolete=False),
                   *to_upgrade]
            ret_code, stdout, stderr = self.run_cmd(cmd)
            exit_code = max(exit_code, ret_code)
            out += stdout
            err += stderr

        return exit_code, out, err

    def run_cmd(self, command: List[str]) -> Tuple[int, str, str]:
        """
        Run command and wait.

        :param command: command to execute
        :return: (exit_code, stdout, stderr)
        """
        self.log.debug("run command: %s", " ".join(command))
        with subprocess.Popen(command,
                              stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as p:
            stdout, stderr = p.communicate()
        self.log.debug("command exit code: %i", p.returncode)

        return p.returncode, stdout.decode(), stderr.decode()

    @staticmethod
    def compare_packages(old, new):
        """
        Compare installed packages and return dictionary with differences.

        :param old: Dict[package_name, version] packages before update
        :param new: Dict[package_name, version] packages after update
        """
        return {"installed": {pkg: new[pkg] for pkg in new if pkg not in old},
                "updated": {pkg: {"old": old[pkg], "new": new[pkg]}
                            for pkg in new
                            if pkg in old and old[pkg] != new[pkg]
                            },
                "removed": {pkg: old[pkg] for pkg in old if pkg not in new}}

    def log_changes(self, changes):
        self.log.info("Installed packages:")
        if changes["installed"]:
            for pkg in changes["installed"]:
                self.log.info("%s %s", pkg, changes["installed"][pkg])
        else:
            self.log.info("None")

        self.log.info("Updated packages:")
        if changes["updated"]:
            for pkg in changes["updated"]:
                self.log.info("%s %s->%s",
                              pkg,
                              changes["updated"][pkg]["old"],
                              changes["updated"][pkg]["new"]
                              )
        else:
            self.log.info("None")

        self.log.info("Removed packages:")
        if changes["removed"]:
            for pkg in changes["removed"]:
                self.log.info("%s %s", pkg, changes["removed"][pkg])
        else:
            self.log.info("None")

    def refresh(self) -> Tuple[int, str, str]:
        """
        Refresh available packages for upgrade.

        :return: (exit_code, stdout, stderr)
        """
        raise NotImplementedError()

    def get_packages(self) -> Dict[str, List[str]]:
        """
        Return the installed packages and their versions.
        """
        raise NotImplementedError()

    def get_action(self, remove_obsolete: bool) -> List[str]:
        """
        Return command for upgrade or upgrade with removing obsolete packages.
        """
        raise NotImplementedError()

    def upgrade_internal(self, remove_obsolete: bool) -> Tuple[int, str, str]:
        """
        Just run upgrade via CLI.
        """
        cmd = [self.package_manager,
               "-y",
               *self.get_action(remove_obsolete)]

        ret_code, stdout, stderr = self.run_cmd(cmd)
        return ret_code, stdout, stderr
