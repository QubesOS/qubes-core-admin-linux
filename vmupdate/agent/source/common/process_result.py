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

from copy import deepcopy


class ProcessResult:
    def __init__(self, code: int = 0, out: str = "", err: str = ""):
        self.code: int = code
        self.out: str = out
        self.err: str = err

    @classmethod
    def process_communicate(cls, proc):
        out, err = proc.communicate()
        code = proc.returncode
        return cls(code, out.decode(), err.decode())

    def __add__(self, other):
        new = deepcopy(self)
        new += other
        return new

    def __iadd__(self, other):
        if not isinstance(other, ProcessResult):
            raise TypeError("unsupported operand type(s) for +:"
                            f"'{self.__class__.__name__}' and "
                            f"'{other.__class__.__name__}'")
        self.code = max(self.code, other.code)
        self.out += other.out
        self.err += other.err
        return self

    def error_from_messages(self):
        out_lines = (self.out + self.err).splitlines()
        if any(line.lower().startswith("err") for line in out_lines):
            self.code = 1
