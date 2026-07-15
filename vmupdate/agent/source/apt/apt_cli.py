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

# pylint: disable=unused-argument

import fcntl
import glob
import os
import contextlib
from typing import List

from source.utils import get_os_data
from source.common.package_manager import PackageManager, AgentType
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT


class APTCLI(PackageManager):
    PROGRESS_REPORTING = False

    # apt addresses releases by codename, but the tool speaks release
    # numbers; extend as new releases appear
    DEBIAN_CODENAMES = {
        11: "bullseye",
        12: "bookworm",
        13: "trixie",
        14: "forky",
    }

    # Every apt source definition that can name a release codename, in both
    # the one-line (`.list`) and deb822 (`.sources`) formats.
    APT_SOURCE_GLOBS = (
        "/etc/apt/sources.list",
        "/etc/apt/sources.list.d/*.list",
        "/etc/apt/sources.list.d/*.sources",
    )

    def __init__(self, log_handler, log_level, agent_type: AgentType):
        super().__init__(log_handler, log_level, agent_type)
        if self.type is AgentType.UPDATE_VM:
            raise NotImplementedError("APT do not support update proxy VM.")
        self.package_manager: str = "apt-get"

        # to prevent a warning: `debconf: unable to initialize frontend: Dialog`
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"

    @contextlib.contextmanager
    def apt_lock(self):
        """
        Contex manager for locking compatible with 'apt-get update' lock.
        """
        with open("/var/lib/apt/lists/lock", "rb+") as f_lock:
            fcntl.lockf(f_lock.fileno(), fcntl.LOCK_EX)
            yield

    def wait_for_lock(self):
        """
        Wait for any other apt-get instance to finish.
        """
        if not os.path.exists("/var/lib/apt/lists/lock"):
            return
        with self.apt_lock():
            pass

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        # apply lock externally to wait for it, until
        # https://bugs.debian.org/1069167 gets implemented
        with self.apt_lock():
            cmd = [
                self.package_manager,
                "-o",
                "Debug::NoLocking=true",
                "-q",
                "update",
            ]
            result = self.run_cmd(cmd)
        # 'apt-get update' reports error with exit code 100, but updater as a
        # whole reserves it for "no updates"
        if result.code == 100:
            result.code = 1
        result.error_from_messages()
        return result

    def get_packages(self):
        """
        Use dpkg-query to return the installed packages and their versions.
        """
        cmd = [
            "dpkg-query",
            "--showformat",
            "${Status} ${Package} ${Version}\n",
            "-W",
        ]
        # EXAMPLE OUTPUT:
        # install ok installed qubes-core-agent 4.1.35-1+deb11u1
        result = self.run_cmd(cmd, realtime=False)

        packages = {}
        for line in result.out.splitlines():
            cols = line.split()
            selection, _flag, status, package, version = cols
            if selection in ("install", "hold") and status == "installed":
                packages.setdefault(package, []).append(version)

        return packages

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Additionally remove obsolete kernels.
        """
        result = super().upgrade_internal(remove_obsolete)

        if remove_obsolete:
            result += self.remove_obsolete_kernels()

        return result

    def get_action(self, remove_obsolete: bool) -> List[str]:
        """
        Return command `upgrade` or `dist-upgrade` if `remove_obsolete`.
        """
        result = [
            "-y",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "-o",
            "Dpkg::Options::=--force-confold",
        ]
        result += ["dist-upgrade"] if remove_obsolete else ["upgrade"]
        return result

    def clean(self) -> int:
        """
        Use apt to clears out the local repository of retrieved package files.
        """
        cmd = [self.package_manager, "clean"]  # consider autoclean
        result = self.run_cmd(cmd, realtime=False)
        return_code = EXIT.ERR_VM_CLEANUP if result.code != 0 else 0
        return return_code

    def remove_obsolete_kernels(self) -> ProcessResult:
        """
        Remove old kernel packages in a little hacky way.

        Usage of `apt autoremove` is not always safe and definitely
        should not be run automatically. However, we want at lest remove
        old kernels since they are heavy and clutter template vms.
        """
        # dry run of autoremove to print which packages will be removed
        # (printed with `Remv ` prefix). It's a simple way to keep apt logic
        # which by default keep 2 newest versions of kernel.
        cmd = [self.package_manager, "autoremove", "-s"]
        result = self.run_cmd(cmd, realtime=False)

        if not result:  # no error
            obsoletes = set()
            for line in result.out.splitlines():
                if line.startswith("Remv"):
                    package_name = line[len("Remv ") :]
                    # consider using wider pattern
                    if package_name.startswith("linux-image"):
                        obsoletes.add(package_name.split(" ")[0])
            if obsoletes:
                cmd = [self.package_manager, "remove", "-y", *obsoletes]
                result = self.run_cmd(cmd, realtime=False)
            else:
                result = ProcessResult(EXIT.OK)

        result.code = EXIT.ERR_VM_CLEANUP if result.code != 0 else 0
        return result

    def _release_upgrade(self, target_version: str) -> ProcessResult:
        """
        Move the qube to the next Debian release.

        apt addresses releases by codename (unlike dnf's ``--releasever``),
        so after bringing the current release fully up to date we rewrite
        the codename across all apt sources, then ``update`` +
        ``dist-upgrade`` onto the new release.
        """
        target = str(target_version).strip()
        os_data = get_os_data(self.log)

        guard = self._verify_release_upgrade(target, os_data)
        if guard.code:
            return guard

        old_codename = os_data["codename"]
        new_codename = self.DEBIAN_CODENAMES[int(target)]

        self._report_progress(0.0)
        result = ProcessResult()

        # bring the current release fully up to date, point the sources at
        # the new release, then update + dist-upgrade onto it; a stale
        # system risks unresolvable transactions across the boundary
        steps = (
            lambda: self.refresh(hard_fail=True),
            lambda: self.upgrade_internal(remove_obsolete=False),
            lambda: self._rewrite_sources(old_codename, new_codename),
            lambda: self.refresh(hard_fail=True),
            lambda: self.upgrade_internal(remove_obsolete=False),
            self._dist_upgrade,
        )
        for step in steps:
            step_result = step()
            result += step_result
            if step_result.code:
                result.code = EXIT.ERR_VM_UPDATE
                return result

        try:
            upgraded_os_data = get_os_data(self.log)
        except OSError as exc:
            msg = f"failed to verify the upgraded Debian release: {exc}"
            self.log.error(msg)
            result += ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=msg)
            return result

        upgraded_release = upgraded_os_data.get("release", "").split(".")[0]
        upgraded_codename = upgraded_os_data.get("codename", "")
        if upgraded_release != target or upgraded_codename != new_codename:
            msg = (
                f"Debian release upgrade did not reach {target} "
                f"({new_codename}); os-release reports "
                f"{upgraded_os_data.get('release')!r} "
                f"({upgraded_codename or 'no codename'})."
            )
            self.log.error(msg)
            result += ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=msg)
            return result

        # old kernels are heavy but non-critical: a cleanup failure must not
        # discard a template that actually reached the new release
        cleanup = self.remove_obsolete_kernels()
        result += cleanup
        if cleanup.code:
            self.log.warning(
                "post-upgrade obsolete-kernel cleanup failed (non-fatal): %s",
                cleanup.err,
            )
        result.code = EXIT.OK

        self._report_progress(100.0)
        return result

    def _dist_upgrade(self) -> ProcessResult:
        """
        Plain `apt-get dist-upgrade`, without APTCLI's obsolete-kernel
        cleanup, so the fatal release bump and the best-effort cleanup
        can fail independently.
        """
        return PackageManager.upgrade_internal(self, remove_obsolete=True)

    def _verify_release_upgrade(
        self, target: str, os_data: dict
    ) -> ProcessResult:
        """
        The shared single-step check plus Debian specifics: the in-qube
        family must be Debian and both codenames must be known.
        """
        if os_data.get("os_family") != "Debian":
            return self._refuse(
                f"in-qube OS family {os_data.get('os_family')!r} is not Debian."
            )

        result = super()._verify_release_upgrade(target, os_data)
        if result.code:
            return result

        if not os_data.get("codename"):
            return self._refuse(
                "cannot read the in-qube release codename from os-release."
            )
        if int(target) not in self.DEBIAN_CODENAMES:
            return self._refuse(
                f"no known Debian codename for release {target!r}; "
                "extend DEBIAN_CODENAMES in apt_cli.py."
            )

        return ProcessResult()

    def _rewrite_sources(
        self, old_codename: str, new_codename: str
    ) -> ProcessResult:
        """
        Substring-replace the old codename with the new across every apt
        source file, covering ``-security``/``-updates``/``-backports``
        variants in both the one-line and deb822 formats. Missing files are
        skipped; I/O errors abort.

        Refuses if no file mentioned the old codename: such sources address
        the release symbolically (e.g. ``stable``), the rewrite would be a
        no-op and the qube would silently stay on the old release.

        The plain substring replace matches the manual ``sed`` procedure in
        qubes-doc; switch to per-field deb822 parsing if it ever over-matches.
        """
        changed = 0
        try:
            for pattern in self.APT_SOURCE_GLOBS:
                for path in glob.glob(pattern):
                    with open(path, "r", encoding="utf-8") as f_src:
                        content = f_src.read()
                    updated = content.replace(old_codename, new_codename)
                    if updated == content:
                        continue  # no codename here -> no spurious write
                    # write-then-rename so a mid-write kill cannot leave a
                    # truncated sources file behind (apt would treat an
                    # empty sources list as "nothing to do" and succeed)
                    tmp_path = path + ".tmp"
                    with open(tmp_path, "w", encoding="utf-8") as f_src:
                        f_src.write(updated)
                        f_src.flush()
                        os.fsync(f_src.fileno())
                    os.replace(tmp_path, path)
                    changed += 1
                    self.log.info(
                        "Rewrote apt source %s: %s -> %s",
                        path,
                        old_codename,
                        new_codename,
                    )
        except OSError as exc:
            msg = f"failed rewriting apt sources: {exc}"
            self.log.error(msg)
            return ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=msg)
        if not changed:
            return self._refuse(
                f"no apt source references codename {old_codename!r}; refusing "
                "to report a release upgrade that would not actually happen."
            )
        return ProcessResult()
