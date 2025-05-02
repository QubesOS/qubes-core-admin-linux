# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2023  Piotr Bartman <prbartman@invisiblethingslab.com>
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
from enum import Enum


class Status(Enum):
    PENDING = "pending"
    UPDATING = "updating"
    DONE = "done"


class FinalStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    NO_UPDATES = "no updates"
    UNKNOWN = "unknown"

    @classmethod
    def __missing__(cls, key):
        return cls.UNKNOWN

    def __bool__(self):
        return self == FinalStatus.SUCCESS


class StatusInfo:
    def __init__(self, qube, status: Status, info):
        self.qname: str = qube.name
        self.status = status
        self.info = info

    @staticmethod
    def pending(qube):
        return StatusInfo(qube, Status.PENDING, info=None)

    @staticmethod
    def updating(qube, percent: float):
        return StatusInfo(qube, Status.UPDATING, info=percent)

    @staticmethod
    def done(qube, status: FinalStatus):
        return StatusInfo(qube, Status.DONE, info=status)


class FormatedLine:
    def __init__(self, qube_name, stream: str, message: str):
        self.qname = qube_name
        self.stream = stream
        self.message = message

    def __str__(self):
        return f"{self.qname}:{self.stream}: {self.message}"