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

import shutil
from typing import List

from source.common.package_manager import PackageManager
from source.common.process_result import ProcessResult


class DNFCLI(PackageManager):
    def __init__(self, loglevel):
        super().__init__(loglevel)
        pck_mng_path = shutil.which('dnf')
        if pck_mng_path is not None:
            pck_mngr = 'dnf'
        else:
            pck_mng_path = shutil.which('yum')
            if pck_mng_path is not None:
                pck_mngr = 'yum'
            else:
                raise RuntimeError("Package manager not found!")
        self.package_manager: str = pck_mngr

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        cmd = [self.package_manager,
               "-q",
               "clean",
               "expire-cache"]
        result = self.run_cmd(cmd)

        cmd = [self.package_manager,
               "-q",
               "check-update",
               f"--setopt=skip_if_unavailable={int(not hard_fail)}"]
        result_check = self.run_cmd(cmd)
        # ret_code == 100 is not an error
        # It means there are packages to be updated
        result_check.code = result_check.code if result_check.code != 100 else 0
        result += result_check
        result.error_from_messages()

        return result

    def get_packages(self):
        """
        Use rpm to return the installed packages and their versions.
        """

        cmd = [
            "rpm",
            "-qa",
            "--queryformat",
            "%{NAME} %{VERSION}%{RELEASE}\n",
        ]
        # EXAMPLE OUTPUT:
        # qubes-core-agent 4.1.351.fc34
        result = self.run_cmd(cmd)

        packages = {}
        for line in result.out.splitlines():
            cols = line.split()
            package, version = cols
            packages.setdefault(package, []).append(version)

        return packages

    def get_action(self, remove_obsolete) -> List[str]:
        """
        Disable or enforce obsolete flag in dnf/yum.
        """
        if remove_obsolete:
            return ["--obsoletes", "upgrade"]
        return ["--setopt=obsoletes=0",
                "upgrade" if self.package_manager == "dnf" else "update"]
