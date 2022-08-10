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


def disable_deltarpm(dnf_conf="/etc/dnf/dnf.conf"):
    """
    Modify dnf.conf file to disable `deltarpm`.
    """
    # TODO dnf makecache
    with open(dnf_conf, "r") as file:
        lines = file.readlines()
        start = lines.index("### QUBES BEGIN ###\n")
        stop = lines.index("### QUBES END ###\n")
        for i, line in enumerate(lines[start:stop]):
            if line.startswith("deltarpm"):
                lines[i + start] = "deltarpm=False\n"
                break
        else:
            lines.insert(stop, "deltarpm=False\n")

    with open(dnf_conf, "w") as file:
        file.writelines(lines)
