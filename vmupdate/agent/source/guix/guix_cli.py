# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2026  The Qubes OS Project
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
import shlex
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

from source.common.package_manager import AgentType, PackageManager
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT


class GUIXCLI(PackageManager):
    PROGRESS_REPORTING = False

    TIME_MACHINE_BRANCH = "master"
    SYSTEM_CONFIG = "/etc/config.scm"
    SYSTEM_PROFILE = "/run/current-system/profile"
    SERVICE_DIR = "/run/qubes-service"
    TIME_MACHINE_ENVIRONMENT = (
        "HOME=/tmp",
        "XDG_CONFIG_HOME=/tmp/qubes-vm-update-guix-config",
        "XDG_CACHE_HOME=/tmp/qubes-vm-update-guix-cache",
    )
    MANIFEST_SEPARATOR = "|"
    STATE_PATHS = {
        "guix-system": "/run/current-system",
    }
    GUIX_CANDIDATES = (
        "/run/qubes/bin/guix",
        "/root/.config/guix/current/bin/guix",
        "/var/guix/profiles/per-user/root/current-guix/bin/guix",
        "/run/current-system/profile/bin/guix",
    )

    def __init__(
        self, log_handler, log_level, agent_type: AgentType
    ):
        super().__init__(log_handler, log_level, agent_type)
        self.package_manager = self._find_guix(self.GUIX_CANDIDATES)

    def _find_guix(self, candidates) -> str:
        for path in candidates:
            if os.access(path, os.X_OK):
                return path

        path = shutil.which("guix")
        if path is not None:
            return path

        raise RuntimeError("Package manager not found!")

    def _uses_qubes_update_proxy(self) -> bool:
        # updates-proxy-setup marks update clients.  A VM with
        # qubes-updates-proxy provides the proxy and must not route its own
        # Guix traffic back through the local forwarder.
        return (
            os.path.exists(os.path.join(self.SERVICE_DIR,
                                       "updates-proxy-setup"))
            and not os.path.exists(os.path.join(self.SERVICE_DIR,
                                                "qubes-updates-proxy"))
        )

    def _with_time_machine_environment(
            self, command: List[str]
    ) -> List[str]:
        env = list(self.TIME_MACHINE_ENVIRONMENT)

        if self._uses_qubes_update_proxy():
            proxy = "http://127.0.0.1:8082/"
            no_proxy = "127.0.0.1,localhost"
            env.extend([
                f"http_proxy={proxy}",
                f"https_proxy={proxy}",
                f"HTTP_PROXY={proxy}",
                f"HTTPS_PROXY={proxy}",
                f"all_proxy={proxy}",
                f"ALL_PROXY={proxy}",
                f"no_proxy={no_proxy}",
                f"NO_PROXY={no_proxy}",
            ])

        return ["env", *env, *command]

    def _run_guix(self, command: List[str]) -> ProcessResult:
        result = self.run_cmd(self._with_time_machine_environment(command))
        if result and not (result.out.strip() or result.err.strip()):
            result.err = (
                f"Guix command failed with exit code {result.code}: "
                f"{shlex.join(command)}"
            )
            result.posted = False
        return result

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Refresh Guix channel metadata for system reconfiguration.

        Use guix time-machine so Qubes vmupdate does not mutate root's Guix
        checkout as package-manager state.  The update target is the Guix
        System generation produced by the later reconfigure step.
        """
        cmd = [
            self.package_manager,
            "time-machine",
            f"--branch={self.TIME_MACHINE_BRANCH}",
            "--",
            "describe",
        ]
        print(
            f"Refreshing Guix channel metadata from "
            f"{self.TIME_MACHINE_BRANCH}.",
            flush=True,
        )
        return self._run_guix(cmd)

    def get_packages(self) -> Dict[str, List[str]]:
        """
        Report Guix System profile entries as update state.

        The shared updater summary compares package/version dictionaries.
        Guix profiles expose manifest entries as name, version, output, and
        store path, so report each system profile output plus the current
        system generation symlink.
        """
        packages: Dict[str, List[str]] = {}
        for name, path in self.STATE_PATHS.items():
            if os.path.exists(path):
                packages[name] = [os.path.realpath(path)]

        result = self._list_installed_packages()
        if result:
            self.log.warning(
                "Unable to list Guix system profile packages: %s",
                result.err or result.out,
            )
            return packages

        for line in result.out.splitlines():
            if not line.strip():
                continue
            entry = self._parse_manifest_entry(line)
            if entry is None:
                self.log.warning(
                    "Ignoring unexpected Guix package entry: %s", line
                )
                continue
            name, version, output, store_path = entry
            package = f"{name}:{output}"
            packages.setdefault(package, []).append(
                f"{version} {store_path}"
            )
        return packages

    def _list_installed_packages(self) -> ProcessResult:
        command = [
            self.package_manager,
            "package",
            f"--profile={self.SYSTEM_PROFILE}",
            "--list-installed",
        ]
        self.log.debug("run command: %s", " ".join(command))
        with subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as proc:
            out, err = proc.communicate()
        out = out.replace(b"\t", self.MANIFEST_SEPARATOR.encode())
        result = ProcessResult.from_untrusted_out_err(out, err)
        result.code = proc.returncode
        self.log.debug("command exit code: %i", result.code)
        return result

    @staticmethod
    def _parse_manifest_entry(line):
        if GUIXCLI.MANIFEST_SEPARATOR in line:
            cols = [
                col.strip()
                for col in line.split(GUIXCLI.MANIFEST_SEPARATOR, 3)
            ]
            if len(cols) == 4 and all(cols):
                return tuple(cols)

        store_marker = "/gnu/store/"
        store_start = line.find(store_marker)
        if store_start != -1:
            store_path = line[store_start:].strip()
            fields = line[:store_start].strip().split()
            if len(fields) >= 3:
                name, version, output = fields[:3]
                return name, version, output, store_path
            entry = GUIXCLI._parse_sanitized_manifest_entry(
                fields, store_path
            )
            if entry is not None:
                return entry

        cols = line.split(None, 3)
        if len(cols) == 4:
            return tuple(cols)

        return None

    @staticmethod
    def _parse_sanitized_manifest_entry(fields, store_path):
        """
        Recover fields after ProcessResult stripped tabs from Guix output.

        Guix separates name, version, output, and store path with tabs.
        ProcessResult removes tabs from untrusted output before callers parse
        it.  When a column is wider than Guix's padding, adjacent fields can be
        glued together; the store item basename keeps the name-version boundary.
        """
        store_item = os.path.basename(store_path)
        try:
            _store_hash, store_name_version = store_item.split("-", 1)
        except ValueError:
            return None

        if len(fields) == 2:
            first, second = fields
            if store_name_version.startswith(f"{first}-"):
                version = store_name_version[len(first) + 1:]
                if second.startswith(version):
                    output = second[len(version):]
                    if output:
                        return first, version, output, store_path

            for index in range(1, len(first)):
                name = first[:index]
                version = first[index:]
                if f"{name}-{version}" == store_name_version:
                    return name, version, second, store_path

        return None

    def get_action(self, remove_obsolete) -> List[str]:
        """
        Kept for the PackageManager interface; upgrade_internal runs the
        reconfiguration through guix time-machine.
        """
        return [
            "time-machine",
            f"--branch={self.TIME_MACHINE_BRANCH}",
            "--",
            "system",
            "reconfigure",
            "--no-bootloader",
            self.SYSTEM_CONFIG,
        ]

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        if not os.path.exists(self.SYSTEM_CONFIG):
            return ProcessResult(
                EXIT.ERR_VM_UPDATE,
                err=f"missing Guix system configuration: {self.SYSTEM_CONFIG}")

        cmd = [self.package_manager, *self.get_action(remove_obsolete)]
        print(
            f"Reconfiguring Guix System from {self.SYSTEM_CONFIG} "
            f"using {self.TIME_MACHINE_BRANCH}.",
            flush=True,
        )
        result = self._run_guix(cmd)
        if not result:
            print("Reconfigured Guix System.", flush=True)
        else:
            print(
                "Guix System reconfiguration failed.",
                file=sys.stderr,
                flush=True,
            )
        return result

    def install_requirements(
            self,
            requirements: Optional[Dict[str, str]],
            curr_pkg: Dict[str, List[str]]
    ) -> ProcessResult:
        """
        Qubes vmupdate plugins do not currently declare Guix requirements.
        Avoid installing ad hoc root profile packages as hidden update policy.
        """
        if requirements:
            packages = ", ".join(sorted(requirements))
            return ProcessResult(
                EXIT.ERR_VM_PRE,
                err=f"Guix vmupdate requirements are unsupported: {packages}")
        return ProcessResult()

    def clean(self) -> int:
        """
        Keep Guix generations for rollback; do not collect garbage implicitly.
        """
        return EXIT.OK
