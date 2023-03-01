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

import os
import logging
from pathlib import Path

LOGPATH = '/var/log/qubes/qubes-update'
FORMAT_LOG = '%(asctime)s [Agent] %(message)s'
LOG_FILE = 'update-agent.log'


def init_logs(
        directory=LOGPATH,
        file=LOG_FILE,
        format_=FORMAT_LOG,
        level="INFO",
        truncate_file=False,
        qname=None,
):
    Path(directory).mkdir(parents=True, exist_ok=True)
    log_path = os.path.join(directory, file)

    if truncate_file:
        with open(log_path, "w"):
            # We want temporary logs here, so we truncate log file.
            # Persistent logs are at dom0.
            pass

    log_handler = logging.FileHandler(log_path, encoding='utf-8')
    log_formatter = logging.Formatter(format_)
    log_handler.setFormatter(log_formatter)

    if qname is not None:
        log = logging.getLogger(qname)
    else:
        log = logging.getLogger('vm-update.agent.PackageManager')
    log.addHandler(log_handler)
    log.propagate = False
    try:
        # if loglevel is unknown just use `DEBUG`
        log.setLevel(level)
        log_level = level
    except (ValueError, TypeError):
        log_level = "DEBUG"
        log.setLevel(log_level)

    return log, log_handler, log_level, log_path, log_formatter
