# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2025  Jayant Saxena <jayantmcom@gmail.com>
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

from unittest.mock import Mock, patch

from vmupdate.agent.source.status import FormatedLine, StatusInfo
from vmupdate.qube_connection import QubeConnection


@patch("vmupdate.qube_connection.shutdown_domains")
def test_wait_for_shutdown_when_vm_started_by_update(shutdown_domains):
    vm = Mock()
    vm.name = "hvm1"
    vm.is_running.side_effect = [False, True]
    vm.devices = {"pci": Mock()}
    vm.devices["pci"].get_assigned_devices.return_value = ["00_1f.2"]
    status_notifier = Mock()
    logger = Mock()

    with QubeConnection(
        vm,
        "/tmp/qubes-update",
        cleanup=False,
        logger=logger,
        show_progress=False,
        status_notifier=status_notifier,
    ):
        pass

    shutdown_domains.assert_called_once_with([vm], logger)
    vm.shutdown.assert_not_called()


@patch("vmupdate.qube_connection.shutdown_domains")
def test_do_not_wait_for_shutdown_without_assigned_pci(shutdown_domains):
    vm = Mock()
    vm.name = "hvm2"
    vm.is_running.side_effect = [False, True]
    vm.devices = {"pci": Mock()}
    vm.devices["pci"].get_assigned_devices.return_value = []
    status_notifier = Mock()
    logger = Mock()

    with QubeConnection(
        vm,
        "/tmp/qubes-update",
        cleanup=False,
        logger=logger,
        show_progress=False,
        status_notifier=status_notifier,
    ):
        pass

    vm.shutdown.assert_called_once_with()
    shutdown_domains.assert_not_called()


@patch("vmupdate.qube_connection.shutdown_domains")
def test_do_not_shutdown_if_vm_was_already_running(shutdown_domains):
    vm = Mock()
    vm.name = "hvm3"
    vm.is_running.return_value = True
    vm.devices = {"pci": Mock()}
    vm.devices["pci"].get_assigned_devices.return_value = ["00_1f.2"]
    status_notifier = Mock()
    logger = Mock()

    with QubeConnection(
        vm,
        "/tmp/qubes-update",
        cleanup=False,
        logger=logger,
        show_progress=False,
        status_notifier=status_notifier,
    ):
        pass

    vm.shutdown.assert_not_called()
    shutdown_domains.assert_not_called()


def test_collect_stderr_drops_repeated_final_milestone():
    """Duplicate 100.0 milestone is not treated as error text."""
    connection = QubeConnection.__new__(QubeConnection)
    connection.qube = Mock()
    connection.qube.name = "fedora-42-xfce"
    connection.logger = Mock()
    connection.status_notifier = Mock()
    proc = Mock()
    proc.stderr = io.BytesIO(b"50.00\n100.00\n100.00\n42.00\nreal error\n")

    connection._collect_stderr(proc)

    items = [
        call.args[0] for call in connection.status_notifier.put.call_args_list
    ]
    progress = [i.info for i in items if isinstance(i, StatusInfo)]
    assert progress == [50.0, 100.0]
    # only the exact repeated 100 milestone is dropped; other numeric
    # stderr (like the stray "42.00") stays visible
    errors = [i.message for i in items if isinstance(i, FormatedLine)]
    assert errors == ["42.00", "real error"]


def test_collect_stdout_handles_none_stdout():
    """_collect_stdout returns empty bytes if proc.stdout is None."""
    connection = QubeConnection.__new__(QubeConnection)
    proc = Mock()
    proc.stdout = None
    assert connection._collect_stdout(proc) == b""
