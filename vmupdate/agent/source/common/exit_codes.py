# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2024  Piotr Bartman-Szwarc <prbartman@invisiblethingslab.com>
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
from dataclasses import dataclass


@dataclass(frozen=True)
class EXIT:
    OK = 0
    OK_NO_UPDATES = 100

    ERR = 1
    ERR_SHUTDOWN_TMPL = 11  # unable to shut down some TemplateVMs
    ERR_SHUTDOWN_APP = 12  # unable to shut down some AppVMs
    ERR_START_APP = 13  # unable to start some AppVMs

    VM_HANDLED = (0, 100, 21, 22, 23, 24, 25)
    ERR_VM = 21
    ERR_VM_PRE = 22
    ERR_VM_REFRESH = 23
    ERR_VM_UPDATE = 24
    ERR_VM_CLEANUP = 25
    ERR_VM_UNHANDLED = 26

    ERR_QREXEX = 40
    ERR_USAGE = 64
    SIGINT = 130
