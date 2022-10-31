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

# pylint: disable=import-outside-toplevel,unused-argument

from .allow_release_info_change import buster_workaround


def get_configured_apt(
        os_data, requirements, log_handler, log_level, no_progress):
    """
    Returns instance of `PackageManager` for apt.

    If `apt` python package is not installed or `no_progress` is `True`
    cli based version is returned.
    """
    try:  # TODO logs
        from .apt_api import APT
    except ImportError:
        # no progress reporting
        no_progress = True

    if no_progress:
        from .apt_cli import APTCLI as APT

    buster_workaround(os_data)

    return APT(log_handler, log_level)
