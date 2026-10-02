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
from logging import Handler
from typing import List
import os
import random
import string
import subprocess

from source.utils import get_os_data
from source.common.package_manager import (
    PackageManager,
    AgentType,
    RELEASE_UPGRADE_ALMOST_DONE,
)
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT


class DNFCLI(PackageManager):
    PROGRESS_REPORTING = False
    UPDATE_VM_INSTALLROOT = "/var/lib/qubes/dom0-updates"

    def __init__(
        self, log_handler: Handler, log_level: int, agent_type: AgentType
    ):
        super().__init__(log_handler, log_level, agent_type)
        for pck_mngr in ("dnf5", "dnf", "yum"):
            if shutil.which(pck_mngr) is not None:
                self.package_manager: str = pck_mngr
                break
        else:
            raise RuntimeError("Package manager not found!")

    def configure_whonix_maybe(self, conf):
        """
        If running inside Whonix gateway, configure DNF options as Whonix
        normally does. Ironically, the DNFCLI class is the only one not needing
        any extra steps, because Whonix installs a wrapper at /usr/bin/dnf, but
        using DNF (either DNF4 or DNF5) via API needs doing it separately.
        Place this function here, as a common place for both DNF and DNF5
        classes.
        """
        # based on condition to start tor in Whonix Gateway
        if not os.path.exists("/usr/share/anon-gw-base-files/gateway"):
            return
        if not os.path.exists(
            "/run/qubes/this-is-netvm"
        ) and not os.path.exists("/run/qubes/this-is-proxyvm"):
            return

        # now we know it's Whonix Gateway
        # apply custom steps of dnf uwt wrapper to the conf object
        # try to keep in sync with https://github.com/Whonix/uwt/blob/master/usr/bin/dnf-3.anondist

        if (
            shutil.which("leaprun")
            and subprocess.call(
                ["leaprun", "--check", "try-wait-for-tor-service-running"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        ):
            subprocess.check_call(
                ["leaprun", "try-wait-for-tor-service-running"]
            )
        else:
            subprocess.check_call(
                ["/usr/libexec/helper-scripts/try-wait-for-tor-service-running"]
            )

        random_socks_password = "".join(
            random.choices(string.ascii_letters + string.digits, k=43)
        )
        conf.proxy = "socks5h://127.0.0.1:9050/"
        conf.proxy_username = "<torS0X>0"
        conf.proxy_password = f"dnf-wrapper_{random_socks_password}"

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        result = self.expire_cache()

        cmd = [
            self.package_manager,
            "-q",
            "check-update",
            "--assumeyes",
            f"--setopt=skip_if_unavailable={int(not hard_fail)}",
        ]
        if self.type != AgentType.UPDATE_VM:
            # In UpdateVM we use preconfigured repos
            result_check = self.run_cmd(cmd)
            # ret_code == 100 is not an error
            # It means there are packages to be updated
            result_check.code = (
                result_check.code if result_check.code != 100 else 0
            )
            result += result_check
            result.error_from_messages()

        return result

    def expire_cache(self) -> ProcessResult:
        """
        Use package manager to expire cache.
        """
        cmd = [self.package_manager, "-q", "clean", "expire-cache"]
        if self.type != AgentType.UPDATE_VM:
            result = self.run_cmd(cmd)
        else:
            # In UpdateVM we use preconfigured repos
            result = ProcessResult()
        return result

    def get_packages(self) -> dict[str, list[str]]:
        """
        Use rpm to return the installed packages and their versions.
        """

        cmd = [
            "rpm",
            "-qa",
            "--queryformat",
            "%{NAME} %{VERSION}-%{RELEASE}\n",
        ]
        # EXAMPLE OUTPUT:
        # qubes-core-agent 4.1.351.fc34
        result = self.run_cmd(cmd, realtime=False)

        packages: dict[str, list[str]] = {}
        for line in result.out.splitlines():
            cols = line.split()
            package, version = cols
            packages.setdefault(package, []).append(version)

        return packages

    def get_action(self, remove_obsolete: bool) -> List[str]:
        """
        Disable or enforce obsolete flag in dnf/yum.
        """
        result = ["-y"]
        if self.type is AgentType.UPDATE_VM:
            result.extend(
                [
                    "upgrade",
                    "--noplugins",
                    "--best",
                    "--allowerasing",
                    "--downloadonly",
                    "--installroot",
                    self.UPDATE_VM_INSTALLROOT,
                    f"--setopt=cachedir={self.UPDATE_VM_INSTALLROOT}/var/cache/dnf",
                    f"--config={self.UPDATE_VM_INSTALLROOT}/etc/dnf/dnf.conf",
                    f"--setopt=reposdir={self.UPDATE_VM_INSTALLROOT}/etc/yum.repos.d",
                    "--exclude=qubes-template-*",
                    "-y",
                ]
            )
            return result
        if remove_obsolete:
            result.extend(["--setopt=obsoletes=1", "upgrade"])
        else:
            result.append("--setopt=obsoletes=0")
            if self.package_manager == "dnf":
                result.append("upgrade")
            else:
                # yum
                result.append("update")
        return result

    def _release_upgrade(self, target_version: str) -> ProcessResult:
        """Move a Fedora/RHEL qube with distro-sync at target releasever."""
        target = str(target_version).strip()

        # _distro_sync uses dnf-only flags (--best, --allowerasing).
        if self.package_manager == "yum":
            return self._refuse("release upgrade requires dnf.")

        try:
            os_data = get_os_data(self.log)
        except OSError as exc:
            return self._refuse(f"cannot read os-release: {exc}")

        guard = self._verify_release_upgrade(target, os_data)
        if guard.code:
            return guard

        self._report_progress(0.0)

        # Clear old-release caches before resolving the target transaction.
        result = self.run_cmd(
            [self.package_manager, "clean", "all"], realtime=False
        )
        if result.code:
            result.code = EXIT.ERR_VM_UPDATE
            return result

        result += self._distro_sync(target)
        if result.code:
            result.code = EXIT.ERR_VM_UPDATE
            return result

        # Verify os-release reflects the target release.
        try:
            upgraded_os_data = get_os_data(self.log)
        except OSError as exc:
            msg = f"failed to verify the upgraded release: {exc}"
            self.log.error(msg)
            result += ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=msg)
            return result

        upgraded_release = upgraded_os_data.get("release", "").split(".")[0]
        if upgraded_release != target:
            msg = (
                f"release upgrade did not reach {target}; os-release "
                f"reports {upgraded_os_data.get('release')!r}."
            )
            self.log.error(msg)
            result += ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=msg)
            return result

        # version_upgrade() owns the jump to 100, after qubes.PostInstall.
        self._report_milestone(RELEASE_UPGRADE_ALMOST_DONE)
        return result

    def _distro_sync(self, target: str) -> ProcessResult:
        """Run distro-sync for the target release via DNF CLI."""
        return self.run_cmd(
            [
                self.package_manager,
                f"--releasever={target}",
                "distro-sync",
                "--best",
                "--allowerasing",
                "--assumeyes",
            ]
        )

    def clean(self) -> int:
        """
        Performs cleanup of temporary files kept for repositories.
        """
        cmd = [self.package_manager, "clean", "packages"]
        result = self.run_cmd(cmd, realtime=False)
        return_code = EXIT.ERR_VM_CLEANUP if result.code != 0 else 0
        return return_code
