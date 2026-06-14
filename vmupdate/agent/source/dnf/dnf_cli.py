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

import sys
import shutil
from typing import List

from source.utils import get_os_data
from source.common.package_manager import PackageManager, AgentType
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT


class DNFCLI(PackageManager):
    PROGRESS_REPORTING = False
    UPDATE_VM_INSTALLROOT = "/var/lib/qubes/dom0-updates"

    def __init__(self, log_handler, log_level, agent_type: AgentType):
        super().__init__(log_handler, log_level, agent_type)
        pck_mng_path = shutil.which("dnf")
        if pck_mng_path is not None:
            pck_mngr = "dnf"
        else:
            pck_mng_path = shutil.which("yum")
            if pck_mng_path is not None:
                pck_mngr = "yum"
            else:
                raise RuntimeError("Package manager not found!")
        self.package_manager: str = pck_mngr

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

    def get_packages(self):
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
        """
        Move the qube to a new Fedora/RHEL release with `distro-sync`.

        Crossing a release boundary is not the same as a normal update:
        we point dnf/yum at the *target* `--releasever` and let
        `distro-sync` converge the whole package set onto that release
        (installing, upgrading, and erasing as needed). A plain `upgrade`
        would leave the system on a mix of old and new release packages.

        dom0 derives `target_version` from `os-*` qvm-features that this
        agent wrote on an earlier boot, and those can drift, so we re-read
        the distribution from inside the qube and refuse on any mismatch
        before doing anything irreversible.

        Progress is reported as bare floats on stderr (one per line),
        which dom0's QubeConnection parses to drive the progress bar.
        0 at the start and 100 once distro-sync succeeds. A whole-release
        distro-sync has no usable fine-grained progress, so the streamed
        package output is what gives the user liveliness.
        """
        target = str(target_version).strip()

        # Re-verify from in-qube data before we touch anything.
        guard = self._verify_release_upgrade(target)
        if guard.code:
            return guard

        self._report_progress(0.0)

        # Wipe metadata and packages cached for the *old* release; otherwise
        # dnf may resolve the transaction against the previous releasever
        # and land on an inconsistent set.
        result = self.run_cmd(
            [self.package_manager, "clean", "all"], realtime=False
        )
        if result.code:
            result.code = EXIT.ERR_VM_UPDATE
            return result

        # The actual release bump.
        upgrade = self.run_cmd(
            [
                self.package_manager,
                f"--releasever={target}",
                "distro-sync",
                "--best",
                "--allowerasing",
                "--assumeyes",
            ]
        )
        if upgrade.code:
            upgrade.code = EXIT.ERR_VM_UPDATE
            result += upgrade
            return result

        result += upgrade
        self._report_progress(100.0)
        return result

    def _verify_release_upgrade(self, target: str) -> ProcessResult:
        """
        Confirm, from inside the qube, that a release upgrade to `target`
        is sane. Returns an errored ProcessResult to abort, or an empty
        (code 0) result to proceed.

        Enforces a single-step jump: the target must be a plain release
        number and exactly one greater than the current in-qube major release.
        This mirrors dom0's `compute_target_version`, ruling out downgrades,
        no-ops, and multi-step jumps.
        """
        if not target.isdigit():
            return self._refuse(f"invalid target release {target!r}.")

        os_data = get_os_data(self.log)
        current_major = os_data.get("release", "").split(".")[0]
        if not current_major.isdigit():
            return self._refuse(
                f"cannot read a numeric in-qube release from "
                f"{os_data.get('release')!r}."
            )
        if int(target) != int(current_major) + 1:
            return self._refuse(
                f"in-qube release {os_data.get('release')!r} can only move "
                f"to {int(current_major) + 1} (single step), not {target!r}."
            )

        return ProcessResult()

    def _refuse(self, reason: str) -> ProcessResult:
        """Log and build the standard "refusing version upgrade" error."""
        msg = f"Refusing version upgrade: {reason}"
        self.log.error(msg)
        return ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=msg)

    @staticmethod
    def _report_progress(percent: float) -> None:
        """
        Emit a progress milestone for dom0's progress bar.

        The agent-to-dom0 progress protocol writes a bare float value
        (one per line) to stderr; dom0's QubeConnection reads these
        to drive the progress bar.  100.0 signals completion.
        """
        print(f"{percent:.2f}", flush=True, file=sys.stderr)

    def clean(self) -> int:
        """
        Performs cleanup of temporary files kept for repositories.
        """
        cmd = [self.package_manager, "clean", "packages"]
        result = self.run_cmd(cmd, realtime=False)
        return_code = EXIT.ERR_VM_CLEANUP if result.code != 0 else 0
        return return_code
