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

import dnf
from dnf.yum.rpmtrans import TransactionDisplay
from dnf.callback import DownloadProgress

from source.common.stream_redirector import StreamRedirector
from source.common.process_result import ProcessResult
from source.common.progress_reporter import ProgressReporter, Progress

from .dnf_cli import DNFCLI


class DNF(DNFCLI):
    def __init__(self, log_handler, log_level):
        super().__init__(log_handler, log_level)
        self.base = dnf.Base()
        self.base.conf.read()  # load dnf.conf
        update = FetchProgress(weight=0)  # % of total time
        fetch = FetchProgress(weight=50)  # % of total time
        upgrade = UpgradeProgress(weight=50)  # % of total time
        self.progress = ProgressReporter(update, fetch, upgrade)

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
                print(100, flush=True)
                return ProcessResult(0, out="", err="Nothing to upgrade")

            with StreamRedirector(result):
                self.base.download_packages(
                    trans.install_set,
                    progress=self.progress.fetch_progress
                )
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


class FetchProgress(DownloadProgress, Progress):
    def __init__(self, weight: int):
        Progress.__init__(self, weight)
        self.bytes_to_fetch = None
        self.bytes_fetched = 0
        self.package_bytes = {}

    def end(self, payload, status, msg):
        """Communicate the information that `payload` has finished downloading.

        :api, `status` is a constant denoting the type of outcome, `err_msg` is an
        error message in case the outcome was an error.

        """
        pass

    def message(self, msg):
        print(msg, flush=True, file=self._stdout)

    def progress(self, payload, done):
        """Update the progress display. :api

        `payload` is the payload this call reports progress for, `done` is how
        many bytes of this payload are already downloaded.

        """
        self.bytes_fetched += done - self.package_bytes.get(payload, 0)
        self.package_bytes[payload] = done
        percent = self.bytes_fetched / self.bytes_to_fetch * 100
        self.notify_callback(percent)

    def start(self, total_files, total_size, total_drpms=0):
        """Start new progress metering. :api

        `total_files` the number of files that will be downloaded,
        `total_size` total size of all files.

        """
        self.bytes_to_fetch = total_size
        self.package_bytes = {}
        self.notify_callback(0)


class UpgradeProgress(TransactionDisplay, Progress):
    def __init__(self, weight: int):
        TransactionDisplay.__init__(self)
        Progress.__init__(self, weight)

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
        if msgs:
            print(msgs)

    def error(self, message):
        """
        Write an error message to the fake stderr.
        """
        print("Error during installation :" + str(message),
              flush=True, file=self._stderr)
