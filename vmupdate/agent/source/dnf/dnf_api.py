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

import io
import os
import sys
import time
from typing import Callable, Optional

import dnf
from dnf.yum.rpmtrans import TransactionDisplay
from dnf.callback import DownloadProgress

from source.common.stream_redirector import StreamRedirector
from source.common.process_result import ProcessResult

from .dnf_cli import DNFCLI


class DNF(DNFCLI):
    def __init__(self, loglevel):
        super().__init__(loglevel)
        self.base = dnf.Base()
        self.base.conf.read()  # load dnf.conf
        self.progress = DNFProgressReporter()

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        self.base.conf.skip_if_unavailable = int(not hard_fail)

        result = ProcessResult()
        try:
            with StreamRedirector(result):
                # Repositories serve as sources of information about packages.
                self.base.read_all_repos()
                updated = self.base.update_cache()
                # A sack is needed for querying.
                self.base.fill_sack()
            if not updated:
                result += ProcessResult(1)
        except Exception as exc:
            result += ProcessResult(2, out="", err=str(exc))

        return result

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Use `dnf` package to upgrade and track progress.
        """
        self.base.conf.obsolete = int(remove_obsolete)

        result = ProcessResult()
        try:
            self.base.upgrade_all()

            self.base.resolve()
            trans = self.base.transaction
            if not trans:
                return 0, "Nothing to upgrade", ""

            with StreamRedirector(result):
                self.base.download_packages(trans.install_set)

                result += sign_check(self.base, trans.install_set)

            if result.code == 0:
                self.base.do_transaction(self.progress.upgrade_progress)
        except Exception as exc:
            result += ProcessResult(3, out="", err=str(exc))
        finally:
            self.base.close()

        return result


def sign_check(base, packages) -> ProcessResult:
    """
    Check signature of packages.
    """
    result = ProcessResult()
    for package in packages:
        ret_code, message = base.package_signature_check(package)
        if ret_code != 0:
            result += ProcessResult(ret_code, out="", err=message)
        else:
            result += ProcessResult(0, out=message, err="")

    return result


# class DNFProgressReporter(TransactionDisplay):
#     """
#     Simple rough progress reporter.
#
#     Implementation of `dnf.yum.rpmtrans.TransactionDisplay`
#     It is assumed that each operation (fetch or install) of each package takes
#     the same amount of time, regardless of its size.
#     """
#
#     def __init__(self, callback: Optional[Callable[[float], None]] = None):
#         super().__init__()
#         self.last_percent = 0.0
#         self.stdout = ""
#         self.stderr = ""
#         if callback is None:
#             self.callback = lambda p: print(f"{p:.2f}", flush=True)
#         else:
#             self.callback = callback
#
#     def progress(self, _package, action, ti_done, ti_total, ts_done, ts_total):
#         """
#         Report ongoing progress on a transaction item.
#
#         :param _package: a package name
#         :param action: the performed action id
#         :param ti_done: number of processed bytes of the transaction item
#         :param ti_total: total number of bytes of the transaction item
#         :param ts_done: number of actions processed in the whole transaction
#         :param ts_total: total number of actions in the whole transaction
#         """
#         fetch = 6
#         install = 7
#         if action not in (fetch, install):
#             return
#         percent = ti_done / ti_total * ts_done / ts_total * 100
#         if self.last_percent < percent:
#             self.callback(percent)
#             self.last_percent = round(percent)
#
#     def scriptout(self, msgs):
#         """
#         Write an output message to the fake stdout.
#         """
#         if msgs:
#             self.stdout += str(msgs) + "\n"
#
#     def error(self, message):
#         """
#         Write an error message to the fake stderr.
#         """
#         self.stderr += str(message) + "\n"



class DNFProgressReporter:
    """
    Simple rough progress reporter.

    It is assumed that updating takes 2%, fetching packages takes 49% and
    installing takes 49% of total time.
    """

    def __init__(self, callback: Optional[Callable[[float], None]] = None):
        saved_stdout = os.dup(sys.stdout.fileno())
        saved_stderr = os.dup(sys.stderr.fileno())
        self.stdout = io.TextIOWrapper(os.fdopen(saved_stdout, 'wb'))
        self.stderr = io.TextIOWrapper(os.fdopen(saved_stderr, 'wb'))
        self.last_percent = 0.0
        if callback is None:
            self.callback = lambda p: \
                print(f"{p:.2f}", flush=True, file=self.stdout)
        else:
            self.callback = callback
        # self.update_progress = DNFProgressReporter.FetchProgress(
        #     self.callback, 0, 2, self.stdout, self.stderr)
        self.fetch_progress = DNFProgressReporter.FetchProgress(
            self.callback, 0, 50, self.stdout, self.stderr)
        self.upgrade_progress = DNFProgressReporter.UpgradeProgress(
            self.callback, 50, 100, self.stdout, self.stderr)

    class _Progress:
        def __init__(
                self,
                callback: Callable[[float], None],
                start, stop,
                stdout: io.TextIOWrapper, stderr: io.TextIOWrapper
        ):
            self.callback = callback
            self.start_percent = start
            self.stop_percent = stop
            self.last_percent = start
            self.stdout = stdout
            self.stderr = stderr

        def notify_callback(self, percent):
            """
            Report ongoing progress.
            """
            time.sleep(2)  # TODO
            _percent = self.start_percent + percent * (
                    self.stop_percent - self.start_percent) / 100
            _percent = round(_percent, 2)
            if self.last_percent < _percent:
                self.callback(_percent)
                self.last_percent = _percent

    class FetchProgress(DownloadProgress, _Progress):
        def __init__(
                self,
                callback: Callable[[float], None],
                start, stop,
                stdout: io.TextIOWrapper, stderr: io.TextIOWrapper
        ):
            DNFProgressReporter._Progress.__init__(
                self, callback, start, stop, stdout, stderr)

        def end(self, payload, status, msg):
            """Communicate the information that `payload` has finished downloading.

            :api, `status` is a constant denoting the type of outcome, `err_msg` is an
            error message in case the outcome was an error.

            """
            print("end", payload, status, msg,
                  flush=True, file=self.stderr)

        def message(self, msg):
            print("message", msg,
                  flush=True, file=self.stderr)

        def progress(self, payload, done):
            """Update the progress display. :api

            `payload` is the payload this call reports progress for, `done` is how
            many bytes of this payload are already downloaded.

            """
            print("progress", payload, done,
                  flush=True, file=self.stderr)

        def start(self, total_files, total_size, total_drpms=0):
            """Start new progress metering. :api

            `total_files` the number of files that will be downloaded,
            `total_size` total size of all files.

            """
            print("start", total_files, total_size, total_drpms,
                  flush=True, file=self.stderr)

    class UpgradeProgress(TransactionDisplay, _Progress):
        def __init__(
                self,
                callback: Callable[[float], None],
                start: int, stop: int,
                stdout: io.TextIOWrapper, stderr: io.TextIOWrapper
        ):
            TransactionDisplay.__init__(self)
            DNFProgressReporter._Progress.__init__(
                self, callback, start, stop, stdout, stderr)

        def progress(self, _package, action, ti_done, ti_total, ts_done,
                     ts_total):
            """
            Report ongoing progress on a transaction item.

            :param _package: a package name
            :param action: the performed action id
            :param ti_done: number of processed bytes of the transaction item
            :param ti_total: total number of bytes of the transaction item
            :param ts_done: number of actions processed in the whole transaction
            :param ts_total: total number of actions in the whole transaction
            """
            fetch = 6
            install = 7
            if action not in (fetch, install):
                return
            percent = ti_done / ti_total * ts_done / ts_total * 100
            self.notify_callback(percent)

        def scriptout(self, msgs):
            """
            Write an output message to the fake stdout.
            """
            print(msgs)

        def error(self, message):
            """
            Write an error message to the fake stderr.
            """
            print("Error during installation :" + str(message),
                  flush=True, file=self.stderr)

