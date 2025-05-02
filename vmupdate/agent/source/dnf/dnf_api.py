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
import subprocess
import dnf
import dnf.conf
import dnf.rpm
from dnf.yum.rpmtrans import TransactionDisplay
from dnf.callback import DownloadProgress
import dnf.transaction

from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT
from source.common.progress_reporter import ProgressReporter, Progress
from source.common.package_manager import AgentType

from .dnf_cli import DNFCLI


class DNF(DNFCLI):
    PROGRESS_REPORTING = True

    def __init__(self, log_handler, log_level, agent_type: AgentType):
        super().__init__(log_handler, log_level, agent_type)

        if self.type == AgentType.UPDATE_VM:
            dnfconf = "/var/lib/qubes/dom0-updates/etc/dnf/dnf.conf"
        else:
            dnfconf = None
        conf = dnf.conf.Conf()
        conf.read(filename=dnfconf)

        if self.type == AgentType.UPDATE_VM:
            conf.best = True
            conf.plugins = False
            conf.installroot = self.UPDATE_VM_INSTALLROOT
            for opt in ('cachedir', 'logdir', 'persistdir'):
                conf.prepend_installroot(opt)
            conf.reposdir = [self.UPDATE_VM_INSTALLROOT + "/etc/yum.repos.d"]
            conf.excludepkgs = ["qubes-template-*"]

            # make sure log file exists
            log_dir = self.UPDATE_VM_INSTALLROOT + "/var/log"
            log_file = os.path.join(log_dir, "hawkey.log")
            os.makedirs(log_dir, exist_ok=True)
            if not os.path.exists(log_file):
                with open(log_file, 'w'):
                    pass

        # Passing `conf` to `base` causes `releasever` not to be set
        subst = conf.substitutions
        if 'releasever' not in subst:
            releasever = dnf.rpm.detect_releasever(conf.installroot)
        subst['releasever'] = releasever

        self.base = dnf.Base(conf)
        if self.type == AgentType.UPDATE_VM:
            self.base._allow_erasing = True
        # Repositories serve as sources of information about packages.
        self.base.read_all_repos()

        update = FetchProgress(weight=10, log=self.log, refresh=True)  # % of total time
        fetch = FetchProgress(weight=45, log=self.log)  # % of total time
        upgrade = UpgradeProgress(weight=45, log=self.log)  # % of total time
        self.progress = ProgressReporter(update, fetch, upgrade)

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        result = ProcessResult()
        self.base.conf.skip_if_unavailable = True
        try:
            self.log.debug("Refreshing available packages...")
            repos = [r for r in self.base.repos.iter_enabled()]
            # we do not know the size of the repositories
            self.progress.update_progress.start(len(repos), len(repos))
            for i, repo in enumerate(repos):
                self.progress.update_progress.progress(repo.id, 1)
                repo.load()
                self.progress.update_progress.end(repo.id, 0, "")
            updated = self.base.update_cache()
            if updated:
                self.log.debug("Cache refresh successful.")
            else:
                self.log.warning("Cache refresh failed.")
                result += ProcessResult(EXIT.ERR_VM_REFRESH)
        except Exception as exc:
            self.log.error(
                "An error occurred while refreshing packages: %s", str(exc))
            result += ProcessResult(EXIT.ERR_VM_REFRESH, out="", err=str(exc))
        return result

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Use `dnf` package to upgrade and track progress.
        """
        self.base.conf.obsolete = int(remove_obsolete)

        result = ProcessResult()
        try:
            self.log.debug("Performing package upgrade...")
            # A sack is needed for querying.
            self.base.fill_sack()

            self.base.upgrade_all()

            # fill empty `Command line` column in dnf history
            self.base.cmds = ["qubes-vm-update"]

            self.base.resolve()
            trans = self.base.transaction
            if not trans:
                self.log.info("No packages to upgrade, quitting.")
                return ProcessResult(EXIT.OK_NO_UPDATES, out="", err="")

            self.base.download_packages(
                trans.install_set,
                progress=self.progress.fetch_progress
            )
            result += sign_check(self.base, trans.install_set, self.log)

            if result.code == EXIT.OK and self.type is not AgentType.UPDATE_VM:
                print("Updating packages.", flush=True)
                self.log.debug("Committing upgrade...")
                self.base.do_transaction(self.progress.upgrade_progress)
                self.log.debug("Package upgrade successful.")
                if self.type is AgentType.VM:
                    self.log.info("Notifying dom0 about installed applications")
                    subprocess.call(['/etc/qubes-rpc/qubes.PostInstall'])
                print("Updated", flush=True)
        except Exception as exc:
            self.log.error(
                "An error occurred while upgrading packages: %s", str(exc))
            result += ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=str(exc))
        finally:
            self.base.close()

        return result


def sign_check(base, packages, log) -> ProcessResult:
    """
    Check a signature of packages.
    """
    log.debug("Check signature of packages.")
    result = ProcessResult()
    for package in packages:
        ret_code, message = base.package_signature_check(package)
        if ret_code != EXIT.OK:
            # Import key and re-try the check
            try:
                base.package_import_key(package, askcb=(lambda a, b, c: True))
            except Exception as ex:
                result += ProcessResult(ret_code, out="", err=str(ex))
                continue
            # base.package_import_key does verify package as a side effect, but
            # do that explicitly anyway, in case the behavior would change
            # (intentionally or not)
            ret_code, message = base.package_signature_check(package)
        if ret_code != EXIT.OK:
            result += ProcessResult(ret_code, out="", err=message)
        else:
            result += ProcessResult(EXIT.OK, out=message, err="")

    return result


class FetchProgress(DownloadProgress, Progress):
    def __init__(self, weight: int, log, refresh: bool = False):
        Progress.__init__(self, weight, log)
        self.bytes_to_fetch = None
        self.bytes_fetched = 0
        self.action = "refresh" if refresh else "fetch"
        self.package_bytes = {}

    def end(self, payload, status, msg):
        """Communicate the information that `payload` has finished downloading.

        :api, `status` is a constant denoting the type of outcome, `err_msg` is
        an error message in case the outcome was an error.
        """
        if status != 0:
            if isinstance(msg, bytes):
                msg = msg.decode('ascii', errors='ignore')
            if msg:
                print(msg, flush=True, file=self._stdout)
        else:
            print(f"{payload}: {self.action.capitalize()}ed", flush=True)

    def message(self, msg):
        if isinstance(msg, bytes):
            msg = msg.decode('ascii', errors='ignore')
        if msg:
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
        self.log.info(f"{self.action.capitalize()} started.")
        self.bytes_to_fetch = total_size
        if self.action == "refresh":
            print("Refreshing available packages.", flush=True)
        else:
            print(f"Fetching {total_files} packages "
                  f"[{self._format_bytes(self.bytes_to_fetch)}]",
                  flush=True)
            self.package_bytes = {}
        self.notify_callback(0)


class UpgradeProgress(TransactionDisplay, Progress):
    def __init__(self, weight: int, log):
        TransactionDisplay.__init__(self)
        Progress.__init__(self, weight, log)

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
        self.log.info(_package)
        fetch = 6
        install = 7
        if action not in (fetch, install):
            return
        percent = (ti_done / ti_total + ts_done - 1) / ts_total * 100
        self.notify_callback(percent)

    def scriptout(self, msgs):
        """
        Write an output message to the fake stdout.
        """
        if msgs:
            if isinstance(msgs, bytes):
                msgs = msgs.decode('ascii', errors='ignore')
            print(msgs, flush=True)

    def filelog(self, package, action):
        print(f"{package}: {dnf.transaction.FILE_ACTIONS[action]}", flush=True)

    def error(self, message):
        """
        Write an error message to the fake stderr.
        """
        if isinstance(message, bytes):
            message = message.decode('ascii', errors='ignore')
        print("Error during installation :" + message,
              flush=True, file=self._stderr)
