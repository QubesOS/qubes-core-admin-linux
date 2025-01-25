#
# The Qubes OS Project, https://www.qubes-os.org/
#
# Copyright (C) 2025 Marek Marczykowski-Górecki
#                           <marmarek@invisiblethingslab.com>
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this library; if not, see <https://www.gnu.org/licenses/>.

import subprocess

def pipewire_archlinux(os_data, log, **kwargs):
    """Help with unattended switch from pulseaudio to pipewire-pulse"""
    # pacman proposes to remove pulseaudio when installing pipewire-pulse,
    # but the default answer is "n", so the update with --noconfirm fails
    # workaround it by removing pulseaudio before the update
    if os_data["os_family"] != 'ArchLinux':
        return
    # check if pulseaudio is installed
    p = subprocess.call(["pacman", "-Q", "pulseaudio"],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    if p != 0:
        return
    # ... and whether pipewire-pulse is going to be installed in the update
    # this will refresh metadata already, before starting progress reporting,
    # but well...
    update_list = subprocess.check_output(["pacman", "-Syup"],
        stderr=subprocess.DEVNULL).decode()
    if not any("/pipewire-pulse-" in line for line in update_list.splitlines()):
        return
    # ... then remove pulseaudio beforehand (temporarily breaking the
    # dependencies)
    log.info("Removing pulseaudio to allow update cleanly migrate to "
             "pipewire-pulse")
    subprocess.check_call(["pacman", "-Rdd", "--noconfirm", "pulseaudio"])
