#!/usr/bin/python3
import os
import sys
import argparse

from source.args import AgentArgs
from source.apt.configuration import get_configured_apt
from source.dnf.configuration import get_configured_dnf
from source.utils import get_os_data


def parse_args(args):
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)
    return args


def main(args=None):
    """
    Run the appropriate package manager.
    """
    args = parse_args(args)
    os_data = get_os_data()
    requirements = {}

    if os_data["os_family"] == "Debian":
        pkg_mng = get_configured_apt(
            os_data, requirements, args.log, args.no_progress)
    elif os_data["os_family"] == "RedHat":
        pkg_mng = get_configured_dnf(
            os_data, requirements, args.log, args.no_progress)
    else:
        raise NotImplementedError(
            "Only Debian and RedHat based OS is supported.")

    # TODO config here
    return_code = pkg_mng.upgrade(refresh=not args.no_refresh,
                                  hard_fail=not args.force_upgrade,
                                  remove_obsolete=not args.leave_obsolete,
                                  requirements=requirements,
                                  print_streams=args.show_output
                                  )
    # TODO clean config

    os.system("/usr/lib/qubes/upgrades-status-notify")

    return return_code


if __name__ == '__main__':
    sys.exit(main())
