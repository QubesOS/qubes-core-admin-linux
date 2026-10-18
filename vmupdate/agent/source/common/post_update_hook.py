# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2026  Andreas Glashauser <ag@andreasglashauser.com>
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
"""qubes.PostUpdate hook, run after every update attempt in a qube"""

import os
import subprocess
from logging import Logger

from .exit_codes import EXIT
from .process_result import ProcessResult

POST_UPDATE_HOOK = "/etc/qubes-rpc/qubes.PostUpdate"


def run_post_update_hook(
    hook_path: str, log: Logger, print_streams: bool = False
) -> ProcessResult:
    """
    Run the hook whether or not the package manager changed anything.

    Unlike qubes.PostInstall, which package managers trigger only after a
    transaction, this hook lets secondary package managers (flatpak, nix,
    pip, ...) refresh on every update run. A missing hook means the qube
    runs a qubes-core-agent without post-update support and is not an error.

    :param hook_path: path of the hook script
    :param log: agent logger
    :param print_streams: forward hook output to std streams
    :return: hook result, `EXIT.OK_POST_UPDATE_CHANGES` if the hook changed
        something, `EXIT.ERR_VM_POST_UPDATE` if the hook failed
    """
    if not os.access(hook_path, os.X_OK):
        log.debug("Post-update hook %s not found, skipping.", hook_path)
        return ProcessResult()
    log.info("Running post-update hook %s", hook_path)
    result = ProcessResult(realtime=print_streams)
    with subprocess.Popen(
        [hook_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        result += ProcessResult.process_communicate(proc)
    if result.code not in (EXIT.OK, EXIT.OK_POST_UPDATE_CHANGES):
        log.warning("Post-update hook failed with exit code: %d", result.code)
        result.code = EXIT.ERR_VM_POST_UPDATE
    _log_output(log, result)
    return result


def merge_hook_result_into_upgrade_code(
    upgrade_code: int, hook_result: ProcessResult
) -> int:
    """
    Combine the package manager's exit code with the hook result.

    Changes made by the hook count as updates even if the package manager
    had none. Not max(): `EXIT.OK_NO_UPDATES` would outrank both the hook
    error code and `EXIT.OK`.

    :param upgrade_code: exit code of the package manager upgrade
    :param hook_result: result of `run_post_update_hook`
    :return: exit code of the whole update run
    """
    if hook_result.code == EXIT.OK_POST_UPDATE_CHANGES:
        return EXIT.OK
    if hook_result:
        return hook_result.code
    return upgrade_code


def _log_output(log: Logger, result: ProcessResult) -> None:
    log_line = log.error if result.code == EXIT.ERR_VM_POST_UPDATE else log.info
    for line in result.out.splitlines():
        log_line("post-update hook out: %s", line)
    for line in result.err.splitlines():
        log_line("post-update hook err: %s", line)
