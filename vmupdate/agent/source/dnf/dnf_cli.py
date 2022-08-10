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
from typing import List, Tuple

from source.common.package_manager import PackageManager


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

    def refresh(self, hard_fail: bool) -> Tuple[int, str, str]:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        out = ""
        err = ""

        cmd = [self.package_manager,
               "-q",
               "clean",
               "expire-cache"]
        ret_code, stdout, stderr = self.run_cmd(cmd)
        exit_code = ret_code
        out += stdout
        err += stderr

        cmd = [self.package_manager,
               "-q",
               "check-update",
               f"--setopt=skip_if_unavailable={int(not hard_fail)}"]
        ret_code, stdout, stderr = self.run_cmd(cmd)
        # ret_code == 100 is not an error
        # It means there are packages to be updated
        ret_code = ret_code if ret_code != 100 else 0
        exit_code = max(ret_code, exit_code)
        out += stdout
        err += stderr

        out_lines = (out + err).splitlines()
        if any(line.startswith("Error:") for line in out_lines):
            exit_code = 1

        return exit_code, out, err

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
        ret_code, stdout, stderr = self.run_cmd(cmd)

        packages = {}
        for line in stdout.splitlines():
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
