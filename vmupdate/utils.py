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
import asyncio
from datetime import datetime

import qubesadmin.exc
from qubesadmin.events.utils import wait_for_domain_shutdown
from vmupdate.agent.source.common.exit_codes import EXIT


def shutdown_domains(to_shutdown, log):
    """
    Try to shut down vms and wait to finish.
    """
    ret_code = EXIT.OK
    wait_for = []
    for vm in to_shutdown:
        try:
            vm.shutdown(force=True)
            wait_for.append(vm)
        except qubesadmin.exc.QubesVMError as exc:
            log.error(str(exc))
            ret_code = EXIT.ERR_SHUTDOWN_APP

    asyncio.run(wait_for_domain_shutdown(wait_for))

    return ret_code, wait_for


def get_feature(vm, feature_name, default_value=None):
    """Get feature, with a working default_value."""
    try:
        return vm.features.get(feature_name, default_value)
    except qubesadmin.exc.QubesDaemonAccessError:
        return default_value


def get_boolean_feature(vm, feature_name, default=False):
    """Helper function to get a feature converted to bool if it exists.

    Necessary because true/false in features are coded as 1/empty string.
    """
    result = get_feature(vm, feature_name, None)
    if result is not None:
        result = bool(result)
    else:
        result = default
    return result


def is_stale(vm, expiration_period):
    """Return True if VM has not been checked for updates recently."""
    today = datetime.today()
    try:
        if not ('qrexec' in vm.features.keys()
                and vm.features.get('os', '') == 'Linux'):
            return False

        last_update_str = vm.features.check_with_template(
            'last-updates-check',
            datetime.fromtimestamp(0).strftime('%Y-%m-%d %H:%M:%S')
        )
        last_update = datetime.fromisoformat(last_update_str)
        if (today - last_update).days > expiration_period:
            return True
    except qubesadmin.exc.QubesDaemonCommunicationError:
        pass
    return False
