# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2024  Marek Marczykowski-Górecki
#                                <marmarek@invisiblethingslab.com>
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
import pathlib
import pkg_resources


def updatesproxy_fix(os_data, log, **kwargs):
    """
    Deploy #9025 fix
    """
    if os_data["os_family"] == "RedHat":
        rpc_filename = "/etc/qubes-rpc/qubes.UpdatesProxy"
        rpc_path = pathlib.Path(rpc_filename)
        # qubes.UpdatesProxy file doesn't exist on template without
        # qubes-core-agent-networking package
        if rpc_path.exists():
            if "STDIO TCP:localhost:8082" in rpc_path.read_text():
                with rpc_path.open("w") as f:
                    f.write("exec socat STDIO TCP4:127.0.0.1:8082\n")
