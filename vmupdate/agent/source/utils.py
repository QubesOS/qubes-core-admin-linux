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

import re
import ast
from typing import Optional, Dict, Any

import pkg_resources


def get_os_data(logger: Optional = None) -> Dict[str, Any]:
    """
    Return dictionary with info about the operating system

    Dictionary contains:
    id: "linux" or a lower-case string identifying the operating system,
    name: "Linux" or a string identifying the operating system,
    codename (optional): an operating system release code name,
    release (optional): packaging.version.Version,
    os_family: "Unknown", "RedHat", "Debian", "ArchLinux".
    """
    data = {}

    os_release = _load_os_release(
        "/etc/os-release",
        "/usr/lib/os-release",
        logger=logger
    )

    data["id"] = os_release.get("ID", "linux").strip()
    data["name"] = os_release.get("NAME", "Linux").strip()
    if "VERSION_ID" in os_release:
        release = os_release["VERSION_ID"]
        data["release"] = pkg_resources.parse_version(release)
    if "VERSION_CODENAME" in os_release:
        data["codename"] = os_release["VERSION_CODENAME"]

    family = [os_release.get('ID', 'linux'),
              *os_release.get('ID_LIKE', '').split()]

    data["os_family"] = 'Unknown'

    if 'debian' in family:
        data["os_family"] = 'Debian'

    if 'rhel' in family or 'fedora' in family:
        data["os_family"] = 'RedHat'

    if 'arch' in family:
        data["os_family"] = 'ArchLinux'

    return data


def _load_os_release(*os_release_files, logger: Optional):
    """
    Load os-release as dictionary.

    More: http://www.freedesktop.org/software/systemd/man/os-release.html
    """
    result = {}
    for filename in os_release_files:
        try:
            with open(filename) as file:
                for line_number, line in enumerate(file):
                    line = line.rstrip()
                    if not line or line.startswith('#'):
                        continue

                    match = re.match(r'([A-Z][A-Z_0-9]+)=(.*)', line)
                    if match:
                        key, val = match.groups()
                        if val and val[0] in '"\'':
                            val = ast.literal_eval(val)
                        result[key] = val
                    else:
                        if logger:
                            logger.error("%s:%i: error in parsing: %s",
                                         filename, line_number, line)
            break
        except OSError as exc:
            if logger:
                logger.info("Could not read file %s: %s", filename, str(exc))

    if not result:
        raise IOError("Failed to read os-release file")

    return result
