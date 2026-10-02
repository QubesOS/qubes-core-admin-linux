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
from typing import Any


class AgentArgs:
    # To avoid code repeating when we want to retrieve arguments
    # Values are heterogeneous: "default" may be None, so not just str.
    OPTIONS: dict[tuple, dict[str, Any]] = {
        ("--log",): {
            "action": "store",
            "default": "INFO",
            "help": "Provide logging level. Values: DEBUG, "
            "INFO (default), WARNING, ERROR, CRITICAL",
        },
        ("--no-refresh",): {
            "action": "store_true",
            "help": "Do not refresh available packages before " "upgrading",
        },
        ("--force-upgrade", "-f"): {
            "action": "store_true",
            "help": "Try upgrade even if errors are "
            "encountered (like a refresh error)",
        },
        ("--no-cleanup",): {
            "action": "store_true",
            "help": "Do not remove cache files after upgrading",
        },
        ("--leave-obsolete",): {
            "action": "store_true",
            "help": "Do not remove updater and cache files from target qube",
        },
        ("--download-only",): {
            "action": "store_true",
            "help": "Only download packages",
        },
        # Hidden: only qvm-template-upgrade may drive an in-place
        # release upgrade; qubes-vm-update rejects it in parse_args.
        ("--version-upgrade",): {
            "action": "store",
            "default": None,
            "help": argparse.SUPPRESS,
        },
    }
    EXCLUSIVE_OPTIONS_1: dict[
        tuple[str] | tuple[str, str] | tuple[str, str, str], dict[str, str]
    ] = {
        ("--show-output", "--verbose", "-v"): {
            "action": "store_true",
            "help": "Show output of management commands",
        },
        ("--quiet", "-q"): {
            "action": "store_true",
            "help": "Do not print anything to stdout",
        },
    }
    EXCLUSIVE_OPTIONS_2: dict[
        tuple[str] | tuple[str, str] | tuple[str, str, str], dict[str, str]
    ] = {
        ("--no-progress",): {
            "action": "store_true",
            "help": "Do not show upgrading progress.",
        },
        ("--just-print-progress",): {
            "action": "store_true",
            "help": argparse.SUPPRESS,
        },
    }
    ALL_OPTIONS: dict[tuple, dict[str, Any]] = {
        **OPTIONS,
        **EXCLUSIVE_OPTIONS_1,
        **EXCLUSIVE_OPTIONS_2,
    }

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        """
        Add common arguments to the parser.
        """
        for arg, properties in AgentArgs.OPTIONS.items():
            parser.add_argument(*arg, **properties)  # type: ignore[arg-type]
        verbosity = parser.add_mutually_exclusive_group()
        for arg, properties in AgentArgs.EXCLUSIVE_OPTIONS_1.items():
            verbosity.add_argument(*arg, **properties)  # type: ignore[arg-type]
        progress_reporting = parser.add_mutually_exclusive_group()
        for arg, properties in AgentArgs.EXCLUSIVE_OPTIONS_2.items():
            progress_reporting.add_argument(*arg, **properties)  # type: ignore[arg-type]

    @staticmethod
    def to_cli_args(args: argparse.Namespace) -> list:
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
                # Value-bearing options default to None when unset (e.g.
                # --version-upgrade on a normal update). Skip those so we
                # never inject a bare "None" into the agent command line.
                arg_value = args_dict[param_name]
                if arg_value is not None:
                    cli_args.extend((keys[0], str(arg_value)))
        return cli_args
