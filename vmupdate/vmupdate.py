#!/usr/bin/python3
"""
Update qubes.
"""
import asyncio
import sys
import argparse
from datetime import datetime

import qubesadmin
import qubesadmin.exc
from qubesadmin.events.utils import wait_for_domain_shutdown
from . import update_manager
from .agent.source.args import AgentArgs


class ArgumentError(Exception):
    """Nonsense arguments
    """


def main(args=None):
    args = parse_args(args)
    app = qubesadmin.Qubes()
    # Load VM list only after dom0 salt call - some new VMs might be created
    try:
        targets = get_targets(args, app)
    except ArgumentError as err:
        print(str(err), file=sys.stderr)
        return 128

    templates = [target for target in targets if target.klass == 'TemplateVM']
    rest = [target for target in targets if target.klass != 'TemplateVM']
    # template qubes first
    exit_code_templates = run_update(templates, args, "templates")
    # then non-template qubes (AppVMs, StandaloneVMs...)
    exit_code_rest = run_update(rest, args)

    restart_app_vms(args, templates)

    return max(exit_code_templates, exit_code_rest)


def parse_args(args):
    parser = argparse.ArgumentParser()

    parser.add_argument('--max-concurrency', action='store',
                        help='Maximum number of VMs configured simultaneously '
                             '(default: %(default)d)',
                        type=int, default=4)

    parser.add_argument('--restart', action='store_true',
                        help='Restart AppVMs whose template has been updated.')

    parser.add_argument('--no-cleanup', action='store_true',
                        help='Do not remove updater files from target qube')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--targets', action='store',
                       help='Comma separated list of VMs to target')
    group.add_argument('--all', action='store_true',
                       help='Target all non-disposable VMs (TemplateVMs and '
                            'AppVMs)')
    group.add_argument('--update-if-stale', action='store',
                       help='DEFAULT. '
                            'Target all TemplateVMs with known updates or for '
                            'which last update check was more than N days '
                            'ago. (default: %(default)d)',
                       type=int, default=7)

    parser.add_argument('--skip', action='store',
                        help='Comma separated list of VMs to be skipped, '
                             'works with all other options.', default="")
    parser.add_argument('--templates', action='store_true',
                        help='Target all TemplatesVMs')
    parser.add_argument('--standalones', action='store_true',
                        help='Target all StandaloneVMs')
    parser.add_argument('--app', action='store_true',
                        help='Target all AppVMs')

    parser.add_argument('--dry-run', action='store_true',
                        help='Just print what happens.')

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
                   if vm.klass != 'DispVM']
    elif args.targets:
        names = args.targets.split(',')
        targets = [vm for vm in app.domains.values() if vm.name in names]
        if len(names) != len(targets):
            target_names = {q.name for q in targets}
            unknowns = set(names) - target_names
            plural = len(unknowns) > 1
            raise ArgumentError(
                f"Unknown qube name{'s' if plural else ''}"
                f": {', '.join(unknowns) if plural else ''.join(unknowns)}"
            )
    else:
        targets += smart_targeting(app, args)

    # remove skipped qubes and dom0 - not a target
    to_skip = args.skip.split(',')
    targets = [vm for vm in targets
               if vm.name != 'dom0' and vm.name not in to_skip]
    return targets


def smart_targeting(app, args):
    targets = []
    for vm in app.domains:
        if getattr(vm, 'updateable', False) and vm.klass != 'AdminVM':
            try:
                to_update = vm.features.get('updates-available', False)
            except qubesadmin.exc.QubesDaemonCommunicationError:
                to_update = False

            if not to_update:
                to_update = stale_update_info(vm, args)

            if to_update:
                targets.append(vm)

    return targets


def stale_update_info(vm, args):
    today = datetime.today()
    try:
        last_update_str = vm.features.get(
            'last-updates-check',
            datetime.fromtimestamp(0).strftime('%Y-%m-%d %H:%M:%S')
        )
        last_update = datetime.fromisoformat(last_update_str)
        if (today - last_update).days > args.update_if_stale:
            return True
    except qubesadmin.exc.QubesDaemonCommunicationError:
        pass
    return False


def run_update(targets, args, qube_klass="qubes"):
    if args.dry_run:
        print(f"Following {qube_klass} will be updated:",
              ",".join((target.name for target in targets)))
        return 0

    runner = update_manager.UpdateManager(targets, args)
    return runner.run(agent_args=args)


def restart_app_vms(args, templates):
    """
    Shutdown running templates and then restart outdated app vms.
    """
    if not args.restart:
        return

    templates_to_shutdown = [template for template in templates
                             if template.is_running()]
    if args.dry_run:
        print("Following templates will be shutdown:",
              ",".join((target.name for target in templates_to_shutdown)))
        # we do not check if any volume is outdated, we expect it will be.
        to_restart = {vm
                      for template in templates
                      for vm in template.appvms
                      if vm.klass in ('AppVM', 'DispVM')
                      and vm.is_running()}
        print("Following qubes CAN be restarted:",
              ",".join((target.name for target in to_restart)))
        return

    # first shutdown templates to apply changes to the root volume
    shutdown_domains(templates_to_shutdown)

    to_restart = {vm
                  for template in templates
                  for vm in template.appvms
                  if vm.klass in ('AppVM', 'DispVM')
                  and vm.is_running()
                  and any(vol.is_outdated() for vol in vm.volumes.values())}

    restart_vms(to_restart)

    # they are no need to start templates automatically


def shutdown_domains(to_shutdown):
    """
    Try to shut down vms and wait to finish.
    """
    wait_for = []
    for vm in to_shutdown:
        try:
            vm.shutdown(force=True)
            wait_for.append(vm)
        except qubesadmin.exc.QubesVMError as exc:
            print(exc, file=sys.stderr)

    asyncio.run(wait_for_domain_shutdown(wait_for))

    return wait_for


def restart_vms(to_restart):
    """
    Try to restart vms.
    """
    shutdowns = shutdown_domains(to_restart)

    # restart shutdown qubes
    for vm in shutdowns:
        try:
            vm.start()
        except qubesadmin.exc.QubesVMError as exc:
            print(exc, file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main())
