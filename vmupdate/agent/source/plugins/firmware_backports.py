# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2025  Marek Marczykowski-Górecki
#                             <marmarek@invisiblethingslab.com>
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
import subprocess

APT_CONF = "/etc/apt/apt.conf.d/01qubes-update"
sources_list = "/etc/apt/sources.list"
backports_line = "deb https://deb.debian.org/debian bookworm-backports main contrib non-free-firmware"
prefs_path = "/etc/apt/preferences.d/firmware_backports"
prefs_data = """\
Package: src:firmware-nonfree
Pin: release n=bookworm-backports
Pin-Priority: 600
"""


def firmware_backports(os_data, log, **kwargs):
    """
    Update firmware packages from backports repository.

    https://github.com/QubesOS/qubes-issues/issues/9815
    """
    if os_data.get("codename", "") == "bookworm":
        # do anything only if firmware package is installed already
        try:
            output = subprocess.check_output(
                ["dpkg", "-l", "firmware-linux-nonfree"],
                stderr=subprocess.DEVNULL,
            )
            if b"bpo" in output:
                # version from backports already installed
                return
        except subprocess.CalledProcessError:
            return
        # first, add bookworm-backports if not already there:
        with open(sources_list) as sources:
            current_sources = sources.read()
        if "bookworm-backports" not in current_sources:
            with open(sources_list, "a") as sources:
                sources.write("\n" + backports_line + "\n")
        # then pin firmware packages to backports repo
        if not os.path.exists(prefs_path):
            with open(prefs_path, "w") as prefs:
                prefs.write(prefs_data)
