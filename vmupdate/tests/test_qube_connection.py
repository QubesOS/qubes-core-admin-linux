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
from unittest.mock import Mock, patch

from vmupdate.qube_connection import QubeConnection


@patch("vmupdate.qube_connection.shutdown_domains")
def test_wait_for_shutdown_when_vm_started_by_update(shutdown_domains):
    vm = Mock()
    vm.name = "hvm1"
    vm.is_running.side_effect = [False, True]
    vm.devices = {'pci': Mock()}
    vm.devices['pci'].get_assigned_devices.return_value = ["00_1f.2"]
    status_notifier = Mock()
    logger = Mock()

    with QubeConnection(
            vm, "/tmp/qubes-update", cleanup=False, logger=logger,
            show_progress=False, status_notifier=status_notifier):
        pass

    shutdown_domains.assert_called_once_with([vm], logger)
    vm.shutdown.assert_not_called()


@patch("vmupdate.qube_connection.shutdown_domains")
def test_do_not_wait_for_shutdown_without_assigned_pci(shutdown_domains):
    vm = Mock()
    vm.name = "hvm2"
    vm.is_running.side_effect = [False, True]
    vm.devices = {'pci': Mock()}
    vm.devices['pci'].get_assigned_devices.return_value = []
    status_notifier = Mock()
    logger = Mock()

    with QubeConnection(
            vm, "/tmp/qubes-update", cleanup=False, logger=logger,
            show_progress=False, status_notifier=status_notifier):
        pass

    vm.shutdown.assert_called_once_with()
    shutdown_domains.assert_not_called()


@patch("vmupdate.qube_connection.shutdown_domains")
def test_do_not_shutdown_if_vm_was_already_running(shutdown_domains):
    vm = Mock()
    vm.name = "hvm3"
    vm.is_running.return_value = True
    vm.devices = {'pci': Mock()}
    vm.devices['pci'].get_assigned_devices.return_value = ["00_1f.2"]
    status_notifier = Mock()
    logger = Mock()

    with QubeConnection(
            vm, "/tmp/qubes-update", cleanup=False, logger=logger,
            show_progress=False, status_notifier=status_notifier):
        pass

    vm.shutdown.assert_not_called()
    shutdown_domains.assert_not_called()
