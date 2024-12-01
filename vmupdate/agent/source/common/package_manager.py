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
""" package manager for VMs """
import io
import logging
import subprocess
import sys
from typing import Optional, Dict, List
from .process_result import ProcessResult
from .exit_codes import EXIT


class PackageManager:
    """ main package manager class """
    def __init__(self, log_handler, log_level):
        self.package_manager: Optional[str] = None
        self.log = logging.getLogger(
            f'vm-update.agent.{self.__class__.__name__}')
        self.log.setLevel(log_level)
        self.log.addHandler(log_handler)
        self.log.propagate = False
        self.requirements: Optional[Dict[str, str]] = None

    def upgrade(
            self,
            refresh: bool,
            hard_fail: bool,
            remove_obsolete: bool,
            print_streams: bool = False
    ):
        """
        Upgrade packages using system package manager.

        :param refresh: refresh available packages first
        :param hard_fail: if refresh or installing requirements fails,
                          stop and fail
        :param remove_obsolete: remove obsolete packages
        :param print_streams: dump captured output to std streams
        :return: return code
        """
        result = self._upgrade(
            refresh, hard_fail, remove_obsolete, self.requirements)
        self._log_output("agent", result)
        if print_streams and not result.posted:
            if result.out:
                print(result.out, flush=True)
            if result.err:
                print(result.err, file=sys.stderr, flush=True)
        return result.code

    def _upgrade(
            self,
            refresh: bool,
            hard_fail: bool,
            remove_obsolete: bool,
            requirements: Optional[Dict[str, str]] = None
    ) -> ProcessResult:
        result = ProcessResult(realtime=True)

        curr_pkg = self.get_packages()

        if requirements:
            print("Install requirements", flush=True)
            result_install = self.install_requirements(requirements, curr_pkg)
            if result_install:
                self.log.warning(
                    "Installing requirements failed with exit code: %d",
                    result_install.code)
                result_install.code = EXIT.ERR_VM_PRE
            result += result_install
            if result and hard_fail:
                self.log.error("Exiting due to a packages install error. "
                               "Use --force-upgrade to upgrade anyway.")
                return result

        if refresh:
            print("Refreshing package info", flush=True)
            result_refresh = self.refresh(hard_fail)
            if result_refresh:
                self.log.warning("Refreshing failed with code: %d",
                                 result_refresh.code)
                result_refresh.code = EXIT.ERR_VM_REFRESH
            result += result_refresh
            if result and hard_fail:
                self.log.error("Exiting due to a refresh error. "
                               "Use --force-upgrade to upgrade anyway.")
                return result

        result_upgrade = self.upgrade_internal(remove_obsolete)
        if result_upgrade:
            result_upgrade.code = EXIT.ERR_VM_UPDATE
        result += result_upgrade

        new_pkg = self.get_packages()

        changes = PackageManager.compare_packages(old=curr_pkg, new=new_pkg)
        summary = self._print_changes(changes)
        if summary:
            summary.code = EXIT.ERR_VM
        result += summary

        if not result and not (changes["installed"] or changes["updated"]):
            result.code = EXIT.OK_NO_UPDATES

        return result

    def _log_output(self, title, result):
        log_as_error = bool(result.code)
        if result.out:
            out_lines = result.out.split("\n")
            log = self.log.error if log_as_error else self.log.info
            for out_line in out_lines:
                log("%s out: %s", title, out_line)
        if result.err:
            err_lines = result.err.split("\n")
            log = self.log.error if log_as_error else self.log.info
            for err_line in err_lines:
                log("%s err: %s", title, err_line)

    def install_requirements(
            self,
            requirements: Optional[Dict[str, str]],
            curr_pkg: Dict[str, List[str]]
    ) -> ProcessResult:
        """
        Make sure if required packages is installed before upgrading.
        """
        if requirements is None:
            requirements = {}

        result = ProcessResult(realtime=True)
        to_install = []  # install latest (ignore version)
        to_upgrade = {}
        for pkg, version in requirements.items():
            if pkg not in curr_pkg:
                to_install.append(pkg)
            else:
                for ver in curr_pkg[pkg]:
                    if version < ver:
                        break
                else:
                    to_upgrade[pkg] = version
        if to_install:
            cmd = [self.package_manager,
                   "-q",
                   "-y",
                   "install",
                   *to_install]
            result += self.run_cmd(cmd)

        if to_upgrade:
            cmd = [self.package_manager,
                   "-q",
                   "-y",
                   *self.get_action(remove_obsolete=False),
                   *to_upgrade]
            result += self.run_cmd(cmd)

        return result

    def run_cmd(
            self, command: List[str], realtime: bool = True) -> ProcessResult:
        """
        Run command and wait.

        :param command: command to execute
        :param realtime: write directly to stdout/stderr
        """
        self.log.debug("run command: %s", " ".join(command))
        if realtime:
            with subprocess.Popen(command, stdin=subprocess.PIPE) as proc:
                result = ProcessResult.process_communicate(proc)
                result.posted = True
        else:
            with subprocess.Popen(command,
                                  stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as proc:
                result = ProcessResult.process_communicate(proc)
        self.log.debug("command exit code: %i", result.code)

        return result

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

    def _print_changes(self, changes):
        result = ProcessResult()
        result.out += self._print_to_string("Installed packages:")
        if changes["installed"]:
            for pkg in sorted(changes["installed"]):
                result.out += self._print_to_string(
                    pkg, changes["installed"][pkg])
        else:
            result.out += self._print_to_string("None")

        result.out += self._print_to_string("Updated packages:")
        if changes["updated"]:
            for pkg in sorted(changes["updated"]):
                result.out += self._print_to_string(
                    pkg,
                    str(changes["updated"][pkg]["old"])[2:-2]
                    + " -> " +
                    str(changes["updated"][pkg]["new"])[2:-2])
        else:
            result.out += self._print_to_string("None")

        result.out += self._print_to_string("Removed packages:")
        if changes["removed"]:
            for pkg in sorted(changes["removed"]):
                result.out += self._print_to_string(
                    pkg, changes["removed"][pkg])
        else:
            result.out += self._print_to_string("None")
        return result

    @staticmethod
    def _print_to_string(*args, **kwargs):
        strio = io.StringIO()
        print(*args, file=strio, **kwargs)
        result = strio.getvalue()
        strio.close()
        return result

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Refresh available packages for upgrade.

        :param hard_fail: raise error if some repo is unavailable
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
        Return command and options for upgrade with optional removing obsoletes.
        """
        raise NotImplementedError()

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Just run upgrade via CLI.
        """
        cmd = [self.package_manager, *self.get_action(remove_obsolete)]

        return self.run_cmd(cmd)

    def clean(self) -> int:
        """
        Clean cache files of package manager.
        Should return 0 on success or EXIT.ERR_VM_CLEANUP otherwise.
        """
        return EXIT.ERR_VM_CLEANUP
