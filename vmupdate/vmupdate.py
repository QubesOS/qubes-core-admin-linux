#!/usr/bin/python3
"""
Update qubes.
"""
import argparse
import asyncio
import logging
import sys
import os
import grp
from datetime import datetime
from typing import Set, Iterable, Dict, Tuple

import qubesadmin
import qubesadmin.exc
from qubesadmin.events.utils import wait_for_domain_shutdown
from vmupdate.agent.source.status import FinalStatus
from . import update_manager
from .agent.source.args import AgentArgs


LOGPATH = '/var/log/qubes/qubes-vm-update.log'
LOG_FORMAT = '%(asctime)s %(message)s'

log_handler = logging.FileHandler(LOGPATH, encoding='utf-8')
log_formatter = logging.Formatter(LOG_FORMAT)
log_handler.setFormatter(log_formatter)

log = logging.getLogger('vm-update')


class ArgumentError(Exception):
    """Nonsense arguments
    """


def main(args=None, app=qubesadmin.Qubes()):
    args = parse_args(args)

    log.setLevel(args.log)
    log.addHandler(log_handler)
    try:
        gid = grp.getgrnam("qubes").gr_gid
        os.chown(LOGPATH, -1, gid)
        os.chmod(LOGPATH, 0o664)
    except (PermissionError, KeyError):
        # do it on best effort basis
        pass

    try:
        targets = get_targets(args, app)
    except ArgumentError as err:
        log.error(str(err))
        return 128

    if not targets:
        if not args.quiet:
            print("No qube selected for update")
        return 100

    independent = [target for target in targets if target.klass in (
        'TemplateVM', 'StandaloneVM')]
    derived = [target for target in targets if target.klass not in (
        'TemplateVM', 'StandaloneVM')]

    # independent qubes first (TemplateVMs, StandaloneVMs)
    ret_code_independent, templ_statuses = run_update(
        independent, args, "templates and stanalones")
    # then derived qubes (AppVMs...)
    ret_code_appvm, _ = run_update(derived, args)

    ret_code_restart = apply_updates_to_appvm(args, independent, templ_statuses)

    return max(ret_code_independent, ret_code_appvm, ret_code_restart)


def parse_args(args):
    parser = argparse.ArgumentParser()

    parser.add_argument('--max-concurrency', '-x',
                        action='store',
                        help='Maximum number of VMs configured simultaneously '
                             '(default: number of cpus)',
                        type=int)

    restart = parser.add_mutually_exclusive_group()
    restart.add_argument(
        '--restart', '--apply-to-sys', '-r',
        action='store_true',
        help='Restart Service VMs whose template has been updated.')
    restart.add_argument(
        '--apply-to-all', '-R', action='store_true',
        help='Restart Service VMs and shutdown AppVMs whose template '
             'has been updated.')

    parser.add_argument('--no-cleanup', action='store_true',
                        help='Do not remove updater files from target qube')

    targets = parser.add_mutually_exclusive_group()
    targets.add_argument('--targets', action='store',
                         help='Comma separated list of VMs to target')
    targets.add_argument('--all', action='store_true',
                         help='Target all non-disposable VMs (TemplateVMs and '
                              'AppVMs)')
    targets.add_argument(
        '--update-if-stale', action='store',
        help='DEFAULT. '
             'Target all TemplateVMs with known updates or for '
             'which last update check was more than N days '
             'ago. (default: %(default)d)',
        type=int, default=7)

    parser.add_argument('--skip', action='store',
                        help='Comma separated list of VMs to be skipped, '
                             'works with all other options.', default="")
    parser.add_argument('--templates', '-T',
                        action='store_true',
                        help='Target all TemplatesVMs')
    parser.add_argument('--standalones', '-S',
                        action='store_true',
                        help='Target all StandaloneVMs')
    parser.add_argument('--app', '-A',
                        action='store_true',
                        help='Target all AppVMs')

    parser.add_argument('--dry-run', action='store_true',
                        help='Just print what happens.')

    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)

    return args


def get_targets(args, app) -> Set[qubesadmin.vm.QubesVM]:
    targets = set()
    if args.templates:
        targets.update([vm for vm in app.domains.values()
                        if vm.klass == 'TemplateVM'])
    if args.standalones:
        targets.update([vm for vm in app.domains.values()
                        if vm.klass == 'StandaloneVM'])
    if args.app:
        targets.update([vm for vm in app.domains.values()
                        if vm.klass == 'AppVM'])
    if args.all:
        # all but DispVMs
        targets.update([vm for vm in app.domains.values()
                        if vm.klass != 'DispVM'])
    elif args.targets:
        names = args.targets.split(',')
        targets = {vm for vm in app.domains.values() if vm.name in names}
        if len(names) != len(targets):
            target_names = {q.name for q in targets}
            unknowns = set(names) - target_names
            plural = len(unknowns) != 1
            raise ArgumentError(
                f"Unknown qube name{'s' if plural else ''}"
                f": {', '.join(unknowns) if plural else ''.join(unknowns)}"
            )
    else:
        targets.update(smart_targeting(app, args))

    # remove skipped qubes and dom0 - not a target
    to_skip = args.skip.split(',')
    if 'dom0' in targets and not args.quiet:
        print("Skipping dom0. To update AdminVM use `qubes-dom0-update`")
    targets = {vm for vm in targets
               if vm.name != 'dom0' and vm.name not in to_skip}
    return targets


def smart_targeting(app, args) -> Set[qubesadmin.vm.QubesVM]:
    targets = set()
    for vm in app.domains:
        if getattr(vm, 'updateable', False) and vm.klass != 'AdminVM':
            try:
                to_update = vm.features.get('updates-available', False)
            except qubesadmin.exc.QubesDaemonCommunicationError:
                to_update = False

            if not to_update:
                to_update = stale_update_info(vm, args)

            if to_update:
                targets.add(vm)

    return targets


def stale_update_info(vm, args):
    today = datetime.today()
    try:
        if not ('qrexec' in vm.features.keys()
                and vm.features.get('os', '') == 'Linux'):
            return False

        last_update_str = vm.features.check_with_template(
            'last-updates-check',
            datetime.fromtimestamp(0).strftime('%Y-%m-%d %H:%M:%S')
        )
        last_update = datetime.fromisoformat(last_update_str)
        if (today - last_update).days > args.update_if_stale:
            return True
    except qubesadmin.exc.QubesDaemonCommunicationError:
        pass
    return False


def run_update(
        targets, args, qube_klass="qubes"
) -> Tuple[int, Dict[str, FinalStatus]]:
    if not targets:
        return 0, {}

    message = f"Following {qube_klass} will be updated:" + \
              ",".join((target.name for target in targets))
    if args.dry_run:
        print(message)
        return 0, {target.name: FinalStatus.SUCCESS for target in targets}
    else:
        log.debug(message)

    runner = update_manager.UpdateManager(targets, args, log=log)
    ret_code, statuses = runner.run(agent_args=args)
    if ret_code:
        log.error("Updating fails with code: %d", ret_code)
    log.debug("Updating report: %s",
              ", ".join((k + ":" + v.value for k, v in statuses.items())))
    return ret_code, statuses


def get_feature(vm, feature_name, default_value=None):
    """Get feature, with a working default_value."""
    try:
        return vm.features.get(feature_name, default_value)
    except qubesadmin.exc.QubesDaemonAccessError:
        return default_value


def get_boolean_feature(vm, feature_name, default=False):
    """helper function to get a feature converted to a Bool if it does exist.
    Necessary because of the true/false in features being coded as 1/empty
    string."""
    result = get_feature(vm, feature_name, None)
    if result is not None:
        result = bool(result)
    else:
        result = default
    return result


def apply_updates_to_appvm(
        args, vm_updated: Iterable, status: Dict[str, FinalStatus]
) -> int:
    """
    Shutdown running templates and then restart/shutdown derived AppVMs.

    Returns return codes:
    `0` - OK
    `1` - unable to shut down some templateVMs
    `2` - unable to shut down some AppVMs
    `3` - unable to start some AppVMs
    """
    if not args.restart and not args.apply_to_all:
        return 0

    updated_tmpls = [
        vm for vm in vm_updated
        if bool(status[vm.name]) and vm.klass == 'TemplateVM'
    ]
    to_restart, to_shutdown = get_derived_vm_to_apply(updated_tmpls)
    templates_to_shutdown = [template for template in updated_tmpls
                             if template.is_running()]

    if args.dry_run:
        print("Following templates will be shutdown:",
              ",".join((target.name for target in templates_to_shutdown)))
        # we do not check if any volume is outdated, we expect it will be.
        print("Following qubes CAN be restarted:",
              ",".join((target.name for target in to_restart)))
        print("Following qubes CAN be shutdown:",
              ",".join((target.name for target in to_shutdown)))
        return 0

    # first shutdown templates to apply changes to the root volume
    # they are no need to start templates automatically
    ret_code, _ = shutdown_domains(templates_to_shutdown)

    if ret_code != 0:
        log.error("Shutdown of some templates fails with code %d", ret_code)
        log.warning(
            "Derived VMs of the following templates will be omitted: %s",
            ", ".join((t.name for t in updated_tmpls if t.is_running())))
        ret_code = 1
        # Some templates are not down dur to errors, there is no point in
        # restarting their derived AppVMs
        ready_templates = [tmpl for tmpl in updated_tmpls
                           if not tmpl.is_running()]
        to_restart, to_shutdown = get_derived_vm_to_apply(ready_templates)

    # both flags `restart` and `apply-to-all` include service vms
    ret_code_ = restart_vms(to_restart)
    ret_code = max(ret_code, ret_code_)
    if args.apply_to_all:
        # there is no need to start plain AppVMs automatically
        ret_code_, _ = shutdown_domains(to_shutdown)
        ret_code = max(ret_code, ret_code_)

    return ret_code


def get_derived_vm_to_apply(templates):
    possibly_changed_vms = set()
    for template in templates:
        possibly_changed_vms.update(template.derived_vms)

    to_restart = set()
    to_shutdown = set()

    for vm in possibly_changed_vms:
        if vm.is_running() and (vm.klass != 'DispVM' or not vm.auto_cleanup):
            if get_boolean_feature(vm, 'servicevm', False):
                to_restart.add(vm)
            else:
                to_shutdown.add(vm)

    return to_restart, to_shutdown


def shutdown_domains(to_shutdown):
    """
    Try to shut down vms and wait to finish.
    """
    ret_code = 0
    wait_for = []
    for vm in to_shutdown:
        try:
            vm.shutdown(force=True)
            wait_for.append(vm)
        except qubesadmin.exc.QubesVMError as exc:
            log.error(str(exc))
            ret_code = 2

    asyncio.run(wait_for_domain_shutdown(wait_for))

    return ret_code, wait_for


def restart_vms(to_restart):
    """
    Try to restart vms.
    """
    ret_code, shutdowns = shutdown_domains(to_restart)

    # restart shutdown qubes
    for vm in shutdowns:
        try:
            vm.start()
        except qubesadmin.exc.QubesVMError as exc:
            log.error(str(exc))
            ret_code = 3

    return ret_code


if __name__ == '__main__':
    sys.exit(main())
