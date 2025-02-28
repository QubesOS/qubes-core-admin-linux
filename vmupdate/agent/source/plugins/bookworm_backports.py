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

sources_list = "/etc/apt/sources.list.d/backports.list"
base_repo_url = "deb.debian.org/debian/dists/bookworm/InRelease"
base_onion_repo_url = "2s4yqjx5ul6okpp3f2gaunr2syex5jgbfpfvhxxbbjwnrsvbk5v3qbid.onion/debian/dists/bookworm/InRelease"
backports_line = (
    "deb {baseurl} bookworm-backports main contrib non-free-firmware\n"
)
prefs_path = "/etc/apt/preferences.d/backports_pins"
prefs_firmware_data = """\
Package: src:firmware-nonfree
Pin: release n=bookworm-backports
Pin-Priority: 600

"""


def add_backports_repo():
    # find URL flavor used for deb.debian.org
    try:
        output = subprocess.check_output(
            ["apt-get", "--print-uris", "update"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return
    baseurl = None
    backports_enabled = False
    for url in output.decode().splitlines():
        if not baseurl and (base_repo_url in url or base_onion_repo_url in url):
            baseurl = (
                url.split()[0]
                .strip("'")
                .replace("/dists/bookworm/InRelease", "")
            )
        if "/debian/dists/bookworm-backports/" in url:
            # backports already enabled
            backports_enabled = True
    # add bookworm-backports if not already there
    if baseurl and not backports_enabled and not os.path.exists(sources_list):
        with open(sources_list, "w") as sources:
            sources.write(backports_line.format(baseurl=baseurl))


def check_package_not_from_backports(package):
    # check if package is installed but not from backports
    try:
        output = subprocess.check_output(
            ["dpkg", "-l", package],
            stderr=subprocess.DEVNULL,
        )
        if b"bpo" not in output:
            # package installed but not from backports
            return True
    except subprocess.CalledProcessError:
        return False
    return False


def bookworm_backports(os_data, log, **kwargs):
    """
    Update firmware packages from backports repository.

    https://github.com/QubesOS/qubes-issues/issues/9815
    """
    if os_data.get("codename", "") == "bookworm":
        # check what packages need to be updated to backports version
        update_firmware = check_package_not_from_backports(
            "firmware-linux-nonfree"
        )
        if not update_firmware:
            return
        add_backports_repo()
        # then pin firmware packages to backports repo
        if not os.path.exists(prefs_path):
            with open(prefs_path, "w") as prefs:
                if update_firmware:
                    prefs.write(prefs_firmware_data)
