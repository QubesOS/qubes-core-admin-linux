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
from packaging import version


def manage_rpm_macro(os_data, log, **kwargs):
    """
    Prepare requirements depend on os version.
    """
    if os_data["os_family"] == "RedHat":
        rpm_macro = "/usr/lib/rpm/macros.d/macros.qubes"
        if (os_data["id"] == "fedora"
                and os_data["release"] < version.parse("33")):
            log.info("Old fedora version detected.")
            with open(rpm_macro, "w") as file:
                file.write("# CVE-2021-20271 mitigation\n"
                           "%_pkgverify_level all")
        else:
            if os.path.exists(rpm_macro):
                os.remove(rpm_macro)
            kwargs["requirements"].update({"dnf": "4.7.0", "rpm": "4.14.2"})
