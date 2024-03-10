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

import os
import pycman
import pyalpm

from source.common.stream_redirector import StreamRedirector
from source.common.process_result import ProcessResult
from source.common.progress_reporter import ProgressReporter, Progress

from .pacman_cli import PACMANCLI


class PACMAN(PACMANCLI):
    def __init__(self, log_handler, log_level):
        super().__init__(log_handler, log_level)

        """Qubes uses a modified binary for pacman in templates, so setting a
        proxy is needed here"""
        if os.path.exists("/run/qubes-service/updates-proxy-setup"):
            proxy = "http://127.0.0.1:8082"
            os.environ["http_proxy"] = proxy
            os.environ["https_proxy"] = proxy

        self.options = None
        self.handle = pycman.config.init_with_config("/etc/pacman.conf")
        self.remotes = self.handle.get_syncdbs()

        self.update = UpdateProgress(
            weight=4, log=self.log, remotes=self.remotes # 4% of total time
        )  
        self.fetch = FetchProgress(
            weight=48, log=self.log # 48% of total time
        )  
        self.upgrade_ = UpgradeProgress(
            weight=48, log=self.log # 48% of total time
        )  
        self.progress = ProgressReporter(
            self.update, self.fetch, self.upgrade_
        )

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        result = ProcessResult()
        t = init_transaction(self.handle, self.options)
        self.handle.dlcb = self.update.download_callback
        try:
            with StreamRedirector(result):
                self.log.debug("Refreshing available packages...")
                for db in self.remotes:
                    success = db.update(force=True)
                if success:
                    self.log.debug("Cache refresh successful.")
                else:
                    self.log.warning("Cache refresh failed.")
                    result += ProcessResult(1)
        except Exception as exc:
            self.log.error(
                "An error occurred while refreshing packages: %s", str(exc)
            )
            result += ProcessResult(2, out="", err=str(exc))
        finally:
            t.release()

        return result

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Use `pycman` package to upgrade and track progress.
        """

        result = ProcessResult()

        t = init_transaction(self.handle, self.options)
        self.handle.dlcb = self.fetch.download_callback
        self.handle.progresscb = self.upgrade_.progress_callback

        self.log.debug("Performing package upgrade...")
        t.sysupgrade(downgrade=False)

        if len(t.to_add) + len(t.to_remove) == 0:
            self.log.info("No packages to upgrade, quitting.")
            t.release()
            return ProcessResult(0, out="", err="")
        try:
            with StreamRedirector(result):
                self.log.debug("Committing upgrade...")
                t.prepare()
                t.commit()
                self.log.debug("Package upgrade successful.")
        except Exception as exc:
            self.log.error(
                "An error occurred while upgrading packages: %s", str(exc)
            )
            result += ProcessResult(3, out="", err=str(exc))
        finally:
            t.release()

        return result


def init_transaction(handle, options):
    "Transaction initialization"
    t = handle.init_transaction(
        cascade=getattr(options, "cascade", False),
        nodeps=getattr(options, "nodeps", False),
        force=getattr(options, "force", False),
        dbonly=getattr(options, "dbonly", False),
        downloadonly=getattr(options, "downloadonly", False),
        nosave=getattr(options, "nosave", False),
        recurse=(getattr(options, "recursive", 0) > 0),
        recurseall=(getattr(options, "recursive", 0) > 1),
        unneeded=getattr(options, "unneeded", False),
        alldeps=(getattr(options, "mode", None) == pyalpm.PKG_REASON_DEPEND),
        allexplicit=(
            getattr(options, "mode", None) == pyalpm.PKG_REASON_EXPLICIT
        ),
    )
    return t


class UpdateProgress(Progress):
    def __init__(self, weight: int, log, remotes):
        Progress.__init__(self, weight, log)
        self.remotes = remotes
        self.remote_count = len(remotes)

    def download_callback(self, filename, tx, total):
        # Dirty hack to see which db we're currently syncing
        for index_of_current_target, target in enumerate(self.remotes):
            if target.name + ".db" == filename:
                break

        percent_current = tx / total * 100
        overall_percent = (
            (index_of_current_target * 100) + percent_current
        ) / self.remote_count

        self.notify_callback(overall_percent)


class FetchProgress(Progress):
    def __init__(self, weight: int, log):
        Progress.__init__(self, weight, log)

    def download_callback(self, filename, tx, total):
        self.notify_callback(tx / total * 100)


class UpgradeProgress(Progress):
    def __init__(self, weight: int, log):
        Progress.__init__(self, weight, log)

    def progress_callback(
        self, target_name, percent, number_of_targets, index_of_current_target
    ):
        "Display progress percentage for target i/n"

        """Dont count progress when the event type doesnt include a package name
         e.g checking disk size and verifying package sigs"""
        if not target_name:
            return
        overall_percent = (
            ((index_of_current_target - 1) * 100) + percent
        ) / number_of_targets
        self.notify_callback(overall_percent)
