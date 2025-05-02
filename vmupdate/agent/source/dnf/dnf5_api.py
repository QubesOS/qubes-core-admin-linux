# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2025  Piotr Bartman-Szwarc
#                             <prbartman@invisiblethingslab.com>
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

import subprocess

import libdnf5
from libdnf5.repo import DownloadCallbacks
from libdnf5.rpm import TransactionCallbacks
from libdnf5.base import Goal

from source.common.process_result import ProcessResult
from source.common.exit_codes import EXIT
from source.common.progress_reporter import ProgressReporter, Progress
from source.common.package_manager import AgentType

from .dnf_cli import DNFCLI


class TransactionError(RuntimeError):
    pass


class DNF5(DNFCLI):
    PROGRESS_REPORTING = True

    def __init__(self, log_handler, log_level, agent_type: AgentType):
        super().__init__(log_handler, log_level, agent_type)

        self.base = libdnf5.base.Base()
        conf = self.base.get_config()

        if self.type == AgentType.UPDATE_VM:
            conf.config_file_path = self.UPDATE_VM_INSTALLROOT + "/etc/dnf/dnf.conf"
            conf.best = True
            conf.plugins = False
            conf.installroot = self.UPDATE_VM_INSTALLROOT
            for opt in ('cachedir', 'logdir', 'persistdir'):
                setattr(conf, opt, self.UPDATE_VM_INSTALLROOT + getattr(conf, opt))
            conf.reposdir = [self.UPDATE_VM_INSTALLROOT + "/etc/yum.repos.d"]
            conf.excludepkgs = ["qubes-template-*"]
        self.base.load_config()

        # Create base object with the loaded config
        self.base.setup()
        self.config = self.base.get_config()
        update = FetchProgress(weight=0, log=self.log)  # % of total time
        fetch = FetchProgress(weight=50, log=self.log)  # % of total time
        upgrade = UpgradeProgress(weight=50, log=self.log)  # % of total time
        self.progress = ProgressReporter(update, fetch, upgrade)

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        self.config.skip_if_unavailable = not hard_fail

        result = ProcessResult()
        try:
            self.log.debug("Refreshing available packages...")

            result += self.expire_cache()

            repo_sack = self.base.get_repo_sack()
            repo_sack.create_repos_from_system_configuration()
            repo_sack.load_repos()
            self.log.debug("Cache refresh successful.")
        except Exception as exc:
            self.log.error(
                "An error occurred while refreshing packages: %s", str(exc))
            result += ProcessResult(EXIT.ERR_VM_REFRESH, out="", err=str(exc))

        return result

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Use `libdnf5` package to upgrade and track progress.
        """
        self.config.obsoletes = remove_obsolete
        result = ProcessResult()
        try:
            self.log.debug("Performing package upgrade...")
            goal = Goal(self.base)
            if self.type == AgentType.UPDATE_VM:
                goal.set_allow_erasing(True)
            goal.add_upgrade("*")
            transaction = goal.resolve()
            # fill empty `Command line` column in dnf history
            transaction.set_description("qubes-vm-update")

            if transaction.get_transaction_packages_count() == 0:
                self.log.info("No packages to upgrade, quitting.")
                return ProcessResult(
                    EXIT.OK_NO_UPDATES, out="",
                    err="\n".join(transaction.get_resolve_logs_as_strings()))

            self.base.set_download_callbacks(
                libdnf5.repo.DownloadCallbacksUniquePtr(
                    self.progress.fetch_progress))
            transaction.download()

            if not transaction.check_gpg_signatures():
                problems = transaction.get_gpg_signature_problems()
                raise TransactionError(
                    f"GPG signatures check failed: {problems}")

            if result.code == EXIT.OK and self.type is not AgentType.UPDATE_VM:
                self.log.debug("Committing upgrade...")
                transaction.set_callbacks(
                    libdnf5.rpm.TransactionCallbacksUniquePtr(
                        self.progress.upgrade_progress))
                tnx_result = transaction.run()
                if tnx_result != transaction.TransactionRunResult_SUCCESS:
                    raise TransactionError(
                        transaction.transaction_result_to_string(tnx_result))
                self.log.debug("Package upgrade successful.")
                if self.type is AgentType.VM:
                    self.log.info("Notifying dom0 about installed applications")
                    subprocess.call(['/etc/qubes-rpc/qubes.PostInstall'])
        except Exception as exc:
            self.log.error(
                "An error occurred while upgrading packages: %s", str(exc))
            result += ProcessResult(EXIT.ERR_VM_UPDATE, out="", err=str(exc))
        return result


class FetchProgress(DownloadCallbacks, Progress):
    def __init__(self, weight: int, log):
        DownloadCallbacks.__init__(self)
        Progress.__init__(self, weight, log)
        self.bytes_to_fetch = 0
        self.bytes_fetched = 0
        self.package_bytes = {}
        self.package_names = {}
        self.count = 0
        self.fetching_notified = False

    def add_new_download(
            self, _user_data, description: str, total_to_download: float
    ) -> int:
        """
        Notify the client that a new download has been created.

        :param _user_data: User data entered together with url/package to download.
        :param description: The message describing new download (url/packagename).
        :param total_to_download: Total number of bytes to download.
        :return: Associated user data for new download.
        """
        self.count += 1
        self.bytes_to_fetch += total_to_download
        self.package_bytes[self.count] = 0
        self.package_names[self.count] = description
        # downloading is not started yet
        self.notify_callback(0)
        return self.count

    def progress(
            self, user_cb_data: int, total_to_download: float, downloaded: float
    ) -> int:
        """
        Download progress callback.

        :param user_cb_data: Associated user data obtained from add_new_download.
        :param total_to_download: Total number of bytes to download.
        :param downloaded: Number of bytes downloaded.
        """
        if not self.fetching_notified:
            print(f"Fetching {self.count} packages "
                  f"[{self._format_bytes(self.bytes_to_fetch)}]",
                  flush=True)
            self.fetching_notified = True
        self.bytes_fetched += downloaded - self.package_bytes[user_cb_data]
        if downloaded > self.package_bytes[user_cb_data]:
            if self.package_bytes[user_cb_data] == 0:
                print(f"Fetching {self.package_names[user_cb_data]} [{self._format_bytes(total_to_download)}]",
                      flush=True)
            self.package_bytes[user_cb_data] = downloaded
            percent = self.bytes_fetched / self.bytes_to_fetch * 100
            self.notify_callback(percent)
        # Should return 0 on success,
        # in case anything in dnf5 changed we return their default value
        return DownloadCallbacks.progress(
            self, user_cb_data, total_to_download, downloaded)

    def end(self, user_cb_data: int, status: int, msg: str) -> int:
        """
        End of download callback.

        :param user_cb_data: Associated user data obtained from add_new_download.
        :param status: The transfer status.
        :param msg: The error message in case of error.
        """
        if status != 0:
            if isinstance(msg, bytes):
                msg = msg.decode('ascii', errors='ignore')
            if msg:
                print(msg, flush=True, file=self._stdout)
        return DownloadCallbacks.end(self, user_cb_data, status, msg)

    def mirror_failure(
            self, user_cb_data: int, msg: str, url: str, metadata: str
    ) -> int:
        """
        Mirror failure callback.

        :param user_cb_data: Associated user data obtained from add_new_download.
        :param msg: Error message.
        :param url: Failed mirror URL.
        :param metadata: the type of metadata that is being downloaded
        """
        if isinstance(msg, bytes):
            msg = msg.decode('ascii', errors='ignore')
        print(f"Fetching {metadata} failure "
              f"({self.package_names[user_cb_data]}) {msg}",
              flush=True, file=self._stdout)
        return DownloadCallbacks.mirror_failure(
            self, user_cb_data, msg, url, metadata)


class UpgradeProgress(TransactionCallbacks, Progress):
    def __init__(self, weight: int, log):
        TransactionCallbacks.__init__(self)
        Progress.__init__(self, weight, log)
        self.pgks = None
        self.pgks_done = None
        self.processed_packages = set()

    def install_progress(
            self, item: libdnf5.base.TransactionPackage, amount: int, total: int
    ):
        r"""
        Report the package installation progress periodically.

        :param item: The TransactionPackage class instance for the package currently being installed
        :param amount: The portion of the package already installed
        :param total: The disk space used by the package after installation
        """
        package = item.get_package().get_full_nevra()
        if package not in self.processed_packages:
            print(f"Installing {package}", flush=True)
            self.processed_packages.add(package)
        pkg_progress = amount / total
        percent = (self.pgks_done + pkg_progress) / self.pgks * 100
        self.notify_callback(percent)

    def transaction_start(self, total: int):
        r"""
        Preparation phase has started.

        :param total: The total number of packages in the transaction
        """
        self.pgks_done = 0
        self.pgks = total

    def uninstall_progress(
            self, item: libdnf5.base.TransactionPackage, amount: int, total: int
    ):
        """
        Report the package removal progress periodically.

        :param item: The TransactionPackage class instance for the package currently being removed
        :param amount: The portion of the package already uninstalled
        :param total: The disk space freed by the package after removal
        """
        package = item.get_package().get_full_nevra()
        if package not in self.processed_packages:
            print(f"Uninstalling {package}", flush=True)
            self.processed_packages.add(package)
        pkg_progress = amount / total
        percent = (self.pgks_done + pkg_progress) / self.pgks * 100
        self.notify_callback(percent)

    def elem_progress(self, item, amount: int, total: int):
        r"""
        The installation/removal process for the item has started

        :param item: The TransactionPackage class instance for the package currently being (un)installed
        :param amount: Index of the package currently being processed. Items are indexed starting from 0.
        :param total: The total number of packages in the transaction
        """
        self.pgks_done = amount
        percent = amount / total * 100
        self.notify_callback(percent)

    def script_start(self, item: libdnf5.base.TransactionPackage, nevra, type: int):
        r"""
        Execution of the rpm scriptlet has started

        :param item: The TransactionPackage class instance for the package that owns the executed or triggered
                     scriptlet. It can be `nullptr` if the scriptlet owner is not part of the transaction
                     (e.g., a package installation triggered an update of the man database, owned by man-db package).
        :param nevra: Nevra of the package that owns the executed or triggered scriptlet.
        :param type: Type of the scriptlet
        """
        print(f"Running rpm scriptlet for {nevra.get_name()}-{nevra.get_epoch()}:{nevra.get_version()}"
              f"-{nevra.get_release()}.{nevra.get_arch()}", flush=True)
