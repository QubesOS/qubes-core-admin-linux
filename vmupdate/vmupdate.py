#!/usr/bin/python3
"""
Update qubes.
"""
import sys
import argparse

import qubesadmin
from . import update_manager
from .agent.source.args import AgentArgs


def main(args=None):
    args = parse_args(args)
    app = qubesadmin.Qubes()
    # Load VM list only after dom0 salt call - some new VMs might be created
    targets = get_targets(args, app)

    # template qubes first
    exit_code_templates = run_update(
        lambda cls: cls == 'TemplateVM', targets, args)
    # then non-template qubes (AppVMs, StandaloneVMs...)
    exit_code_rest = run_update(
        lambda cls: cls != 'TemplateVM', targets, args)
    return max(exit_code_templates, exit_code_rest)


def parse_args(args):
    parser = argparse.ArgumentParser()

    parser.add_argument('--max-concurrency', action='store',
                        help='Maximum number of VMs configured simultaneously '
                             '(default: %(default)d)',
                        type=int, default=4)

    parser.add_argument('--no-cleanup', action='store_true',
                        help='Do not remove updater files from target qube')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--targets', action='store',
                       help='Comma separated list of VMs to target')
    group.add_argument('--all', action='store_true',
                       help='Target all non-disposable VMs (TemplateVMs and '
                            'AppVMs)')

    parser.add_argument('--templates', action='store_true',
                        help='Target all TemplatesVMs')
    parser.add_argument('--standalones', action='store_true',
                        help='Target all StandaloneVMs')
    parser.add_argument('--app', action='store_true',
                        help='Target all AppVMs')

    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)

    return args


def get_targets(args, app):
    targets = []
    if args.templates:
        targets += [vm for vm in app.domains.values()
                    if vm.klass == 'TemplateVM']
    if args.standalones:
        targets += [vm for vm in app.domains.values()
                    if vm.klass == 'StandaloneVM']
    if args.app:
        targets += [vm for vm in app.domains.values()
                    if vm.klass == 'AppVM']
    if args.all:
        # all but DispVMs
        targets = [vm for vm in app.domains.values()
                   if not vm.klass == 'DispVM']
    elif args.targets:
        names = args.targets.split(',')
        targets = [vm for vm in app.domains.values() if vm.name in names]

    # remove dom0 - not a target
    targets = [vm for vm in targets if vm.name != 'dom0']
    return targets


def run_update(qube_predicator, targets, args):
    qubes_to_go = [vm for vm in targets if qube_predicator(vm.klass)]
    runner = update_manager.UpdateManager(qubes_to_go,
                                          show_output=args.show_output,
                                          max_concurrency=args.max_concurrency,
                                          quiet=args.quiet,
                                          cleanup=not args.no_cleanup,
                                          )
    return runner.run(agent_args=args)


if __name__ == '__main__':
    sys.exit(main())
