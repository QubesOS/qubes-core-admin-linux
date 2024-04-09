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
import argparse


class AgentArgs:
    # To avoid code repeating when we want to retrieve arguments
    OPTIONS = {
        ("--log",): {"action": 'store',
                     "default": "INFO",
                     "help": 'Provide logging level. Values: DEBUG, '
                             'INFO (default), WARNING, ERROR, CRITICAL'},
        ("--no-refresh",): {"action": 'store_true',
                            "help": 'Do not refresh available packages before '
                                    'upgrading'},
        ("--force-upgrade", "-f"):
            {"action": 'store_true',
             "help": 'Try upgrade even if errors are '
                     'encountered (like a refresh error)'},
        ("--leave-obsolete",): {
            "action": 'store_true',
            "help": 'Do not remove obsolete packages during upgrading'},
    }
    EXCLUSIVE_OPTIONS_1 = {
        ("--show-output", "--verbose", "-v"):
            {"action": 'store_true',
             "help": 'Show output of management commands'},
        ("--quiet", "-q"): {"action": 'store_true',
                            "help": 'Do not print anything to stdout'}
    }
    EXCLUSIVE_OPTIONS_2 = {
        ("--no-progress",): {"action": "store_true",
                             "help": "Do not show upgrading progress."},
        ("--just-print-progress",): {"action": "store_true",
                                     "help": argparse.SUPPRESS}
    }
    ALL_OPTIONS = {**OPTIONS, **EXCLUSIVE_OPTIONS_1, **EXCLUSIVE_OPTIONS_2}

    @staticmethod
    def add_arguments(parser):
        """
        Add common arguments to the parser.
        """
        for arg, properties in AgentArgs.OPTIONS.items():
            parser.add_argument(*arg, **properties)
        verbosity = parser.add_mutually_exclusive_group()
        for arg, properties in AgentArgs.EXCLUSIVE_OPTIONS_1.items():
            verbosity.add_argument(*arg, **properties)
        progress_reporting = parser.add_mutually_exclusive_group()
        for arg, properties in AgentArgs.EXCLUSIVE_OPTIONS_2.items():
            progress_reporting.add_argument(*arg, **properties)

    @staticmethod
    def to_cli_args(args):
        """
        Parse selected args values to flags ready to pass
        to an agent entrypoint.
        """
        args_dict = vars(args)

        cli_args = []
        for keys, value in AgentArgs.ALL_OPTIONS.items():
            # keys[0] since first value is used as attribute name in parser
            param_name = keys[0][2:].replace("-", "_")
            if value["action"] == "store_true":
                if args_dict[param_name]:
                    cli_args.append(keys[0])
            else:
                cli_args.extend((keys[0], args_dict[param_name]))
        return cli_args
