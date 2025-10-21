# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2025  Ali Mirjamali <ali@mirjamali.com>
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

import subprocess

from pathlib import Path

def flatpak_updater(os_data, log, **kwargs):
    """
    Distro agnostic plugin to update system-wide flatpaks
    """

    if not Path("/usr/bin/flatpak"):
        return
    log.info("Flatpak is installed. Checking for flatpak updates.")
    if not subprocess.Popen(
        ["flatpak", "remote-ls", "--system", "--updates"],
        stdout=subprocess.PIPE
    ).stdout.read().decode().strip():
        log.info("No flatpak updates found.")
        return
    log.info("Flatpak updates found. Updating flatpaks...")
    subprocess.run(
        ["flatpak", "update", "--system", "--noninteractive", "-y"]
    )
