# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
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


class NIXOSCLI(PackageManager):
    def __init__(self, log_handler, log_level):
        super().__init__(log_handler, log_level)
        self.package_manager = "qubes-nixos-rebuild"

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        Note: Is a no-op in NixOS because the qubes-nixos-rebuild
        wrapper takes care of it, and having just sync could cause problems.

        :return: (exit_code, stdout, stderr)
        """
        cmd = ["true"]
        return self.run_cmd(cmd)

    def get_packages(self) -> Dict[str, List[str]]:
        """
        Use nix to return the installed packages and their versions.
        """

        cmd = ["qubes-nixos-get-packages"]
        # EXAMPLE OUTPUT:
        # qubes-core-agent-linux: ∅ → 4.3.5, +1413.6 KiB
        # python3: ∅ → 3.11.9, 3.12.4, +229814.3 KiB
        # dns-root-data: ∅ → 2024-06-20

        result = self.run_cmd(cmd, realtime=False)

        packages: Dict[str, List[str]] = {}
        for line in result.out.splitlines():
            package, info = line.split(":", 1)
            versions = info.lstrip("∅ → ").split(", ")
            for version in versions:
                if not version.startswith("+"):
                    packages.setdefault(package, []).append(version)

        return packages

    def get_action(self, remove_obsolete) -> List[str]:
        """
        qubes-nixos-rebuild will handle obsoletions itself
        """
        return []
