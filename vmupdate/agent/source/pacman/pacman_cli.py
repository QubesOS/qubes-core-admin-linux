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

from typing import List, Dict

from source.common.package_manager import PackageManager
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT


class PACMANCLI(PackageManager):
    def __init__(self, log_handler, log_level):
        super().__init__(log_handler, log_level)
        self.package_manager = "pacman"

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        Note: Is a no-op in ArchLinux because upgrade takes care of it, and
        having just sync could cause problems.
        See: https://github.com/QubesOS/qubes-core-admin-linux/pull/139#pullrequestreview-1845574713

        :return: (exit_code, stdout, stderr)
        """
        cmd = ["true"]
        return self.run_cmd(cmd)

    def get_packages(self) -> Dict[str, List[str]]:
        """
        Use pacman to return the installed packages and their versions.
        """

        cmd = [self.package_manager, "-Q"]
        # EXAMPLE OUTPUT:
        # qubes-vm-core 4.2.25-1
        result = self.run_cmd(cmd, realtime=False)

        packages: Dict[str, List[str]] = {}
        for line in result.out.splitlines():
            package, version = line.split()
            packages.setdefault(package, []).append(version)

        return packages

    def get_action(self, remove_obsolete) -> List[str]:
        """
        Pacman will handle obsoletions itself
        """
        return ["--noconfirm", "-Syu"]

    def clean(self) -> int:
        """
        Clean cache files of package manager.
        Should return 0 on success or EXIT.ERR_VM_CLEANUP otherwise.
        """
        cmd = [self.package_manager, "-Scc"]  # consider -Sc
        result = self.run_cmd(cmd, realtime=False)
        return_code = EXIT.ERR_VM_CLEANUP if result.code != 0 else 0
        return return_code
