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
from typing import Tuple, Callable, Optional

import dnf
from dnf.yum.rpmtrans import TransactionDisplay

from source.common.stream_redirector import StreamRedirector

from .dnf_cli import DNFCLI


class DNF(DNFCLI):
    def __init__(self, loglevel):
        super().__init__(loglevel)
        self.progress = DNFProgressReporter()
        self.base = dnf.Base()
        self.base.conf.read()  # load dnf.conf

    def refresh(self, hard_fail: bool) -> Tuple[int, str, str]:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        self.base.conf.skip_if_unavailable = int(not hard_fail)

        captured_stdout = io.BytesIO()
        captured_stderr = io.BytesIO()
        try:
            with StreamRedirector(captured_stdout, captured_stderr):
                # Repositories serve as sources of information about packages.
                self.base.read_all_repos()
                updated = self.base.update_cache()
                # A sack is needed for querying.
                self.base.fill_sack()
            if updated:
                ret_code = 0
            else:
                ret_code = 2
            errors = ""
        except Exception as exc:
            ret_code = 2
            errors = str(exc)

        captured_stdout.flush()
        captured_stdout.seek(0, io.SEEK_SET)
        stdout = captured_stdout.read().decode()
        captured_stderr.flush()
        captured_stderr.seek(0, io.SEEK_SET)
        stderr = errors + captured_stderr.read().decode()
        return ret_code, stdout, stderr

    def upgrade_internal(self, remove_obsolete: bool) -> Tuple[int, str, str]:
        """
        Use `dnf` package to upgrade and track progress.
        """
        self.base.conf.obsolete = int(remove_obsolete)

        try:
            exit_code = 0
            self.base.upgrade_all()

            self.base.resolve()
            trans = self.base.transaction
            if not trans:
                return exit_code, "Nothing to upgrade", ""

            self.base.download_packages(trans.install_set)

            ret_code = sign_check(
                self.base, trans.install_set, self.progress.stderr)
            exit_code = max(exit_code, ret_code)

            if exit_code == 0:
                self.base.do_transaction(self.progress)
        except Exception as exc:
            stderr = self.progress.stderr + "\n" + str(exc)
            return 1, self.progress.stdout, stderr
        finally:
            self.base.close()

        return exit_code, self.progress.stdout, self.progress.stderr


def sign_check(base, packages, output):
    """
    Check signature of packages.
    """
    exit_code = 0
    for package in packages:
        ret_code, message = base.package_signature_check(package)
        if ret_code != 0:
            exit_code = max(exit_code, ret_code)
            output += message
    return exit_code


class DNFProgressReporter(TransactionDisplay):
    """
    Simple rough progress reporter.

    Implementation of `dnf.yum.rpmtrans.TransactionDisplay`
    It is assumed that each operation (fetch or install) of each package takes
    the same amount of time, regardless of its size.
    """

    def __init__(self, callback: Optional[Callable[[float], None]] = None):
        super().__init__()
        self.last_percent = 0.0
        self.stdout = ""
        self.stderr = ""
        if callback is None:
            self.callback = lambda p: print(f"{p:.2f}%", flush=True)
        else:
            self.callback = callback

    def progress(self, _package, action, ti_done, ti_total, ts_done, ts_total):
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
        if self.last_percent < percent:
            self.callback(percent)
            self.last_percent = round(percent)

    def scriptout(self, msgs):
        """
        Write an output message to the fake stdout.
        """
        if msgs:
            for msg in msgs:
                self.stdout += msg + "\n"

    def error(self, message):
        """
        Write an error message to the fake stderr.
        """
        self.stderr += str(message) + "\n"
