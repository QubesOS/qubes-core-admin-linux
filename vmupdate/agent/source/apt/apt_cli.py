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

import csv
import fcntl
import glob
import os
import contextlib
from typing import List, Iterator, Optional
from logging import Handler

from source.utils import get_os_data
from source.common.package_manager import (
    PackageManager,
    AgentType,
    RELEASE_UPGRADE_ALMOST_DONE,
)
from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT


class APTCLI(PackageManager):
    PROGRESS_REPORTING = False

    # Maintained by Debian's distro-info-data package, which is a
    # qubes-core-agent dependency (including in minimal templates).
    DEBIAN_RELEASES_FILE = "/usr/share/distro-info/debian.csv"

    # APT source files in one-line (.list) and deb822 (.sources) formats.
    APT_SOURCE_GLOBS = (
        "/etc/apt/sources.list",
        "/etc/apt/sources.list.d/*.list",
        "/etc/apt/sources.list.d/*.sources",
    )

    def __init__(
        self, log_handler: Handler, log_level: int, agent_type: AgentType
    ) -> None:
        super().__init__(log_handler, log_level, agent_type)
        if self.type is AgentType.UPDATE_VM:
            raise NotImplementedError("APT do not support update proxy VM.")
        self.package_manager: str = "apt-get"

        # to prevent a warning: `debconf: unable to initialize frontend: Dialog`
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"

    @contextlib.contextmanager
    def apt_lock(self) -> Iterator:
        """
        Contex manager for locking compatible with 'apt-get update' lock.
        """
        with open("/var/lib/apt/lists/lock", "rb+") as f_lock:
            fcntl.lockf(f_lock.fileno(), fcntl.LOCK_EX)
            yield

    def wait_for_lock(self) -> None:
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

    def get_packages(self) -> dict[str, list[str]]:
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

        packages: dict[str, list[str]] = {}
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
        """Upgrade to the target Debian release by rewriting apt source codenames and running dist-upgrade."""
        target = str(target_version).strip()
        os_data = get_os_data(self.log)

        guard = self._verify_release_upgrade(target, os_data)
        if guard.code:
            return guard

        old_codename = os_data["codename"]
        try:
            new_codename = self._debian_codename(target)
        except (OSError, csv.Error) as exc:
            return self._refuse(
                f"cannot read Debian release data from "
                f"{self.DEBIAN_RELEASES_FILE}: {exc}."
            )
        if not new_codename:
            return self._refuse(
                f"no Debian codename for release {target!r} in "
                f"{self.DEBIAN_RELEASES_FILE}."
            )

        # Refuse early if no source file references the old or new codename.
        try:
            sources = [
                open(path, "r", encoding="utf-8").read()
                for path in self._apt_source_paths()
            ]
        except OSError as exc:
            return self._refuse(f"cannot read apt sources: {exc}.")
        if not any(
            old_codename in content or new_codename in content
            for content in sources
        ):
            return self._refuse(
                f"no apt source references codename {old_codename!r} "
                f"or {new_codename!r}; refusing upgrade."
            )

        self._report_progress(0.0)
        result = ProcessResult()

        # Update before switching sources and running dist-upgrade: a stale
        # system risks unresolvable transactions across the release boundary.
        # Each step gets its own slice of the bar, proportional to its
        # weight. The weights are the wall-clock share of each step in a
        # complete Debian 12-to-13 template upgrade run with --log DEBUG:
        # 6.6, 6.5, ~0, 9.1, 21.9 and 55.8 percent, rounded (source
        # rewriting takes no measurable time but keeps a visible weight
        # of 1). To re-measure after changing the steps, run an upgrade
        # with --log DEBUG, take the timestamps where each step starts in
        # the log, and divide every step's duration by the total. Only
        # the ratios matter; the code normalizes by the sum.
        # (bar weight, drives fetch/install phases, step)
        steps = (
            (7, False, lambda: self.refresh(hard_fail=True)),
            (7, True, lambda: self.upgrade_internal(remove_obsolete=False)),
            (
                1,
                False,
                lambda: self._rewrite_sources(old_codename, new_codename),
            ),
            (9, False, lambda: self.refresh(hard_fail=True)),
            (22, True, lambda: self.upgrade_internal(remove_obsolete=False)),
            (54, True, self._dist_upgrade),
        )
        total = sum(weight for weight, _, _ in steps)
        done = 0
        for weight, installs, step in steps:
            self._set_progress_step(
                done / total * RELEASE_UPGRADE_ALMOST_DONE,
                (done + weight) / total * RELEASE_UPGRADE_ALMOST_DONE,
                installs,
            )
            done += weight
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

        # Kernel cleanup failure must not discard a completed upgrade.
        cleanup = self.remove_obsolete_kernels()
        result += cleanup
        if cleanup.code:
            self.log.warning(
                "post-upgrade obsolete-kernel cleanup failed (non-fatal): %s",
                cleanup.err,
            )
        result.code = EXIT.OK

        # version_upgrade() owns the jump to 100, after qubes.PostInstall.
        self._report_milestone(RELEASE_UPGRADE_ALMOST_DONE)
        return result

    def _dist_upgrade(self) -> ProcessResult:
        """Run dist-upgrade without kernel cleanup so failures are independent."""
        return PackageManager.upgrade_internal(self, remove_obsolete=True)

    def _verify_release_upgrade(
        self, target: str, os_data: dict
    ) -> ProcessResult:
        """
        The shared single-step check plus the current Debian codename check.
        """
        result = super()._verify_release_upgrade(target, os_data)
        if result.code:
            return result

        if not os_data.get("codename"):
            return self._refuse(
                "cannot read the in-qube release codename from os-release."
            )

        return ProcessResult()

    @classmethod
    def _debian_codename(cls, target: str) -> Optional[str]:
        """Return Debian's codename for a numeric release."""
        with open(
            cls.DEBIAN_RELEASES_FILE,
            "r",
            encoding="utf-8",
            newline="",
        ) as releases_file:
            releases = csv.reader(releases_file)
            next(releases, None)  # header
            for row in releases:
                # Columns: version,codename,series,created,release,eol,...
                # Unreleased rows stop after "created", so pad before
                # unpacking; sid/experimental have an empty version.
                version, _codename, series, _created, released = (
                    row + [""] * 5
                )[:5]
                if version != target:
                    continue
                if not released:
                    # distro-info lists unreleased versions too;
                    # not a released Debian yet, so no codename.
                    return None
                return series or None
        return None

    @classmethod
    def _apt_source_paths(cls) -> List[str]:
        """Every existing apt source file matched by APT_SOURCE_GLOBS."""
        return [
            path
            for pattern in cls.APT_SOURCE_GLOBS
            for path in glob.glob(pattern)
        ]

    def _rewrite_sources(
        self, old_codename: str, new_codename: str
    ) -> ProcessResult:
        """Replace the old release codename with the new one in all apt source files.

        Refuses if no source references either codename (e.g. symbolic 'stable' sources).
        """
        changed = 0
        try:
            for path in self._apt_source_paths():
                with open(path, "r", encoding="utf-8") as f_src:
                    content = f_src.read()
                updated = content.replace(old_codename, new_codename)
                if updated == content:
                    if new_codename in content:
                        # Already rewritten by an interrupted run.
                        changed += 1
                    continue  # no codename here -> no spurious write
                # Atomic write: preserve symlinks, permissions, and ownership.
                real_path = os.path.realpath(path)
                st = os.stat(real_path)
                tmp_path = real_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f_src:
                    f_src.write(updated)
                    f_src.flush()
                    os.fsync(f_src.fileno())
                os.chmod(tmp_path, st.st_mode)
                os.chown(tmp_path, st.st_uid, st.st_gid)
                os.replace(tmp_path, real_path)
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
                f"no apt source references codename {old_codename!r}; "
                "refusing upgrade."
            )
        return ProcessResult()
