# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2024  Piotr Bartman <prbartman@invisiblethingslab.com>
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

APT_CONF = "/etc/apt/apt.conf.d/01qubes-update"


def apt_keep_old_conffiles(os_data, log, **kwargs):
    """
    Always chose default behavior for when conflicts in apt conffiles appears.
    """
    if os_data["os_family"] != "Debian":
        return

    option = '''Dpkg::Options {
   "--force-confdef";
   "--force-confold";
}'''
    with open(APT_CONF, "w") as file:
        file.write(f'\n{option}\n')
