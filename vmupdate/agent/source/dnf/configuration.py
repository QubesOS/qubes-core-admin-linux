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

# pylint: disable=import-outside-toplevel

from .manage_rpm_macro import manage_rpm_macro
from .disable_deltarpm import disable_deltarpm


def get_configured_dnf(os_data, requirements, loglevel, no_progress):
    """
    Returns instance of `PackageManager` for dnf.

    If `dnf` python package is not installed or `no_progress` is `True`
    cli based version is returned.
    """
    try:  # TODO logs
        from .dnf_api import DNF
    except ImportError:
        # no progress reporting
        no_progress = True

    if no_progress:
        from .dnf_cli import DNFCLI as DNF

    manage_rpm_macro(os_data, requirements)
    disable_deltarpm()

    return DNF(loglevel)
