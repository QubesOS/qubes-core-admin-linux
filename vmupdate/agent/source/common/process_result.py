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
import sys
from copy import deepcopy
from typing import Union, Optional
from .exit_codes import EXIT


class ProcessResult:
    """
    Representation of system process output: (exit code, out, err).

    Controls where the results of subprocesses are directed
    (e.g., to stdout or buffered).
    """
    def __init__(
            self,
            code: int = EXIT.OK, out: str = "", err: str = "",
            realtime: bool = False
    ):
        self.code: int = code
        self.out: str = out
        self.err: str = err
        self.realtime = realtime
        self.posted = False
        if self.realtime and not self.posted:
            if self.out:
                print(self.out, file=sys.stdout, flush=True)
            if self.err:
                print(self.err, file=sys.stderr, flush=True)
            self.posted = True

    @classmethod
    def process_communicate(cls, proc):
        result = cls.from_untrusted_out_err(*proc.communicate())
        result.code = proc.returncode
        return result

    @classmethod
    def from_untrusted_out_err(
            cls,
            untrusted_out: Optional[Union[str, bytes]],
            untrusted_err: Optional[Union[str, bytes]] = ""
    ):
        if untrusted_out is None:
            untrusted_out_bytes = b''
        elif isinstance(untrusted_out, str):
            untrusted_out_bytes: bytes = untrusted_out.encode()
        else:
            untrusted_out_bytes = untrusted_out
        out = ProcessResult.sanitize_output(untrusted_out_bytes)

        if untrusted_err is None:
            untrusted_err_bytes = b''
        elif isinstance(untrusted_err, str):
            untrusted_err_bytes: bytes = untrusted_err.encode()
        else:
            untrusted_err_bytes = untrusted_err
        err = ProcessResult.sanitize_output(untrusted_err_bytes)

        return cls(EXIT.OK, out, err)

    @staticmethod
    def sanitize_output(untrusted_bytes: bytes, single: bool = False) -> str:
        untrusted_str = untrusted_bytes.decode('ascii', errors='ignore')
        return ''.join([c for c in untrusted_str
                        if 0x20 <= ord(c) <= 0x7e
                        or (c == '\n' and not single)])

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
        if self.realtime and not other.posted:
            if other.out:
                print(other.out, file=sys.stdout, flush=True)
            if other.err:
                print(other.err, file=sys.stderr, flush=True)
            other.posted = True
        return self

    def __bool__(self):
        return bool(self.code)

    def __repr__(self):
        return f"{self.code}; {self.out}; {self.err}"

    def error_from_messages(self):
        out_lines = (self.out + '\n' + self.err).splitlines()
        if any(line.lower().startswith("err") for line in out_lines):
            self.code = EXIT.ERR
