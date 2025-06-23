#!/usr/bin/python3
"""
Update qubes.
"""
import argparse
import asyncio
import logging
import subprocess
import sys
import os
import grp
from datetime import datetime
from typing import Set, Iterable, Dict, Tuple

import qubesadmin
import qubesadmin.exc
from qubesadmin.events.utils import wait_for_domain_shutdown
from vmupdate.agent.source.status import FinalStatus
from vmupdate.agent.source.common.exit_codes import EXIT
from . import update_manager
from .agent.source.args import AgentArgs

DEFAULT_UPDATE_IF_STALE = 7
LOGPATH = '/var/log/qubes/qubes-vm-update.log'
LOG_FORMAT = '%(asctime)s %(message)s'


class ArgumentError(Exception):
    """Nonsense arguments
    """


def main(args=None, app=qubesadmin.Qubes()):
    args = parse_args(args, app)

    log_handler = logging.FileHandler(LOGPATH, encoding='utf-8')
    log_formatter = logging.Formatter(LOG_FORMAT)
    log_handler.setFormatter(log_formatter)

    log = logging.getLogger('vm-update')
    log.setLevel(args.log)
    log.addHandler(log_handler)
    try:
        gid = grp.getgrnam("qubes").gr_gid
        os.chown(LOGPATH, -1, gid)
        os.chmod(LOGPATH, 0o664)
    except (PermissionError, KeyError):
        # do it on the best effort basis
        pass

    try:
        targets = get_targets(args, app)
    except ArgumentError as err:
        print(str(err), file=sys.stderr)
        log.error(str(err))
        return EXIT.ERR_USAGE

    if not targets:
        if not args.quiet:
            print("No qube selected for update")
        return EXIT.OK_NO_UPDATES if args.signal_no_updates else EXIT.OK

    admin = [target for target in targets if target.klass == 'AdminVM']
    independent = [target for target in targets if target.klass in (
        'TemplateVM', 'StandaloneVM')]
    derived = [target for target in targets if target.klass not in (
        'AdminVM', 'TemplateVM', 'StandaloneVM')]

    no_updates = True
    ret_code_admin = EXIT.OK
    if admin:
        message = f"The admin VM ({admin[0].name}) will be updated."
    else:
        message = "The admin VM will not be updated."
    if args.dry_run:
        print(message)
    elif admin:
        log.debug(message)
        if args.just_print_progress and args.no_refresh:
            # internal usage just for installing ready updates, use carefully
            ret_code_admin, admin_status = run_update(admin, args, log, "admin VM")
        else:
            # use qubes-dom0-update to update dom0
            ret_code_admin, admin_status = run_admin_update(admin[0], args, log)
        no_updates = all(stat == FinalStatus.NO_UPDATES
                         for stat in admin_status.values())

    # independent qubes first (TemplateVMs, StandaloneVMs)
    ret_code_independent, templ_statuses = run_update(
        independent, args, log, "templates and standalones")
    no_updates = all(stat == FinalStatus.NO_UPDATES
                     for stat in templ_statuses.values()) and no_updates
    # then derived qubes (AppVMs...)
    ret_code_appvm, app_statuses = run_update(derived, args, log)
    no_updates = all(stat == FinalStatus.NO_UPDATES
                     for stat in app_statuses.values()) and no_updates

    ret_code_restart = apply_updates_to_appvm(
        args, independent, templ_statuses, app_statuses, log)

    ret_code = max(ret_code_admin, ret_code_independent, ret_code_appvm, ret_code_restart)
    if ret_code == EXIT.OK and no_updates and args.signal_no_updates:
        return EXIT.OK_NO_UPDATES
    if ret_code == EXIT.OK_NO_UPDATES and not args.signal_no_updates:
        return EXIT.OK
    return ret_code


def parse_args(args, app):
    parser = argparse.ArgumentParser()
    try:
        default_update_if_stale = int(app.domains["dom0"].features.get(
            "qubes-vm-update-update-if-stale", DEFAULT_UPDATE_IF_STALE))
    except qubesadmin.exc.QubesDaemonAccessError:
        default_update_if_stale = DEFAULT_UPDATE_IF_STALE

    parser.add_argument('--max-concurrency', '-x',
                        action='store',
                        help='Maximum number of VMs configured simultaneously '
                             '(default: number of cpus)',
                        type=int)
    parser.add_argument('--dry-run', action='store_true',
                        help='Just print what happens.')
    parser.add_argument(
        '--signal-no-updates', action='store_true',
        help='Return exit code 100 instead of 0 '
             'if there is no updates available.')

    restart = parser.add_mutually_exclusive_group()
    restart.add_argument(
        '--apply-to-sys', '--restart', '-r',
        action='store_true',
        help='Restart not updated ServiceVMs whose template has been updated.')
    restart.add_argument(
        '--apply-to-all', '-R', action='store_true',
        help='Restart not updated ServiceVMs and shutdown not updated AppVMs '
             'whose template has been updated.')
    restart.add_argument(
        '--no-apply', action='store_true',
        help='DEFAULT. Do not restart/shutdown any AppVMs.')

    update_state = parser.add_mutually_exclusive_group()
    update_state.add_argument(
        '--force-update', action='store_true',
        help='Attempt to update all targeted VMs '
             'even if no updates are available')
    update_state.add_argument(
        '--update-if-stale', action='store',
        help='DEFAULT. '
             'Attempt to update targeted VMs with known updates available '
             'or for which last update check was more than N days ago. '
             '(default: %(default)d)',
        type=int, default=default_update_if_stale)
    update_state.add_argument(
        '--update-if-available', action='store_true',
        help='Update targeted VMs with known updates available.')

    parser.add_argument(
        '--skip', action='store',
        help='Comma separated list of VMs to be skipped, '
             'works with all other options.', default="")
    parser.add_argument(
        '--targets', action='store',
        help='Comma separated list of VMs to target. Ignores conditions.')
    parser.add_argument(
        '--templates', '-T', action='store_true',
        help='Target all updatable TemplateVMs.')
    parser.add_argument(
        '--standalones', '-S', action='store_true',
        help='Target all updatable StandaloneVMs.')
    parser.add_argument(
        '--apps', '-A', action='store_true',
        help='Target running updatable AppVMs to update in place.')
    parser.add_argument(
        '--all', action='store_true',
        help='DEFAULT. Target all updatable VMs except AdminVM. '
             'Use explicitly with "--targets" to include both.')

    # for internal usage, e.g., download updates via proxy vm
    parser.add_argument(
        '--display-name', action='store',
        help=argparse.SUPPRESS)

    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)

    if args.update_if_stale < 0:
        raise ArgumentError("Wrong value for --update-if-stale")

    return args


def get_targets(args, app) -> Set[qubesadmin.vm.QubesVM]:
    preselected_targets = preselect_targets(args, app)
    selected_targets = select_targets(preselected_targets, args)
    return selected_targets


def preselect_targets(args, app) -> Set[qubesadmin.vm.QubesVM]:
    targets = set()
    updatable = {vm for vm in app.domains if getattr(vm, 'updateable', False)}
    default_targeting = (not args.templates and not args.standalones and
                         not args.apps and not args.targets)
    if args.all or default_targeting:
        # filter out stopped AppVMs and DispVMs (?)
        targets = {vm for vm in updatable
                   if vm.klass not in ("AppVM", "DispVM") or vm.is_running()}
    else:
        # if not all updatable are included, target a specific classes
        if args.templates:
            targets.update([vm for vm in updatable
                            if vm.klass == 'TemplateVM'])
        if args.standalones:
            targets.update([vm for vm in updatable
                            if vm.klass == 'StandaloneVM'])
        if args.apps:
            targets.update({vm for vm in updatable
                            if vm.klass == 'AppVM' and vm.is_running()})

    # user can target non-updatable vm if she like
    if args.targets:
        names = args.targets.split(',')
        explicit_targets = {vm for vm in app.domains if vm.name in names}
        if len(names) != len(explicit_targets):
            target_names = {q.name for q in explicit_targets}
            unknowns = set(names) - target_names
            plural = len(unknowns) != 1
            raise ArgumentError(
                f"Unknown qube name{'s' if plural else ''}"
                f": {', '.join(unknowns) if plural else ''.join(unknowns)}"
            )
        targets.update(explicit_targets)

    # remove skipped qubes and dom0 - not a target
    to_skip = args.skip.split(',')
    targets = {vm for vm in targets if vm.name not in to_skip}

    # exclude vms with `skip-update` feature, but allow --targets to override it
    if not args.targets:
        targets = {vm for vm in targets
               if not bool(vm.features.get('skip-update', False))}

    return targets


def select_targets(targets, args) -> Set[qubesadmin.vm.QubesVM]:
    # try to update all preselected targets
    if args.force_update:
        return targets

    selected = set()
    for vm in targets:
        try:
            to_update = vm.features.get('updates-available', False)
        except qubesadmin.exc.QubesDaemonCommunicationError:
            to_update = False

        # there are updates available => select
        if to_update:
            selected.add(vm)
            continue

        # update vm only if there are updates available
        # and that's not true at this point => skip
        if args.update_if_available:
            continue

        if is_stale(vm, expiration_period=args.update_if_stale):
            selected.add(vm)

    return selected


def is_stale(vm, expiration_period):
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
        if (today - last_update).days > expiration_period:
            return True
    except qubesadmin.exc.QubesDaemonCommunicationError:
        pass
    return False


def run_admin_update(admin_vm, args, log):
    cmd = ["qubes-dom0-update", "-y"]
    if args.quiet:
        cmd.append('--quiet')
    if args.just_print_progress:
        cmd.append("--just-print-progress")
    elif args.signal_no_updates:
        # --just-print-progress checks it by default
        proc = subprocess.Popen(["qubes-dom0-update", "--check-only"])
        proc.wait()
        if proc.returncode == 0:
            return proc.returncode, {admin_vm.name: FinalStatus.NO_UPDATES}
    proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
    proc.wait()
    if proc.returncode == 0:
        status = FinalStatus.SUCCESS
    elif proc.returncode == 100:
        status = FinalStatus.NO_UPDATES
    else:
        status = FinalStatus.ERROR
    return proc.returncode, {admin_vm.name: status}


def run_update(
        targets, args, log, qube_klass="qubes"
) -> Tuple[int, Dict[str, FinalStatus]]:
    if targets:
        message = f"Following {qube_klass} will be updated: " + \
                  ", ".join((target.name for target in targets))
    else:
        if qube_klass == "qubes":
            message = ""  # no need to inform about app VMs etc.
        else:
            message = f"No {qube_klass} will be updated."
    if args.dry_run:
        if message:
            print(message)
        return EXIT.OK, {target.name: FinalStatus.SUCCESS for target in targets}
    else:
        log.debug(message)

    if not targets:
        return EXIT.OK, {}

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
        args,
        vm_updated: Iterable,
        template_statuses: Dict[str, FinalStatus],
        derived_statuses: Dict[str, FinalStatus],
        log
) -> int:
    """
    Shutdown running templates and then restart/shutdown derived AppVMs.

    Returns return codes:
    `0` - OK
    `11` - unable to shut down some templateVMs
    `12` - unable to shut down some AppVMs
    `13` - unable to start some AppVMs
    """
    if not args.apply_to_sys and not args.apply_to_all:
        return EXIT.OK

    updated_tmpls = [
        vm for vm in vm_updated
        if bool(template_statuses[vm.name]) and vm.klass == 'TemplateVM'
    ]
    to_restart, to_shutdown = get_derived_vm_to_apply(
        updated_tmpls, derived_statuses)
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
        return EXIT.OK

    # first shutdown templates to apply changes to the root volume
    # they are no need to start templates automatically
    ret_code, _ = shutdown_domains(templates_to_shutdown, log)

    if ret_code != EXIT.OK:
        log.error("Shutdown of some templates fails with code %d", ret_code)
        log.warning(
            "Derived VMs of the following templates will be omitted: %s",
            ", ".join((t.name for t in updated_tmpls if t.is_running())))
        ret_code = EXIT.ERR_SHUTDOWN_TMPL
        # Some templates are not down dur to errors, there is no point in
        # restarting their derived AppVMs
        ready_templates = [tmpl for tmpl in updated_tmpls
                           if not tmpl.is_running()]
        to_restart, to_shutdown = get_derived_vm_to_apply(
            ready_templates, derived_statuses)

    # both flags `restart` and `apply-to-all` include service vms
    ret_code_ = restart_vms(to_restart, log)
    ret_code = max(ret_code, ret_code_)
    if args.apply_to_all:
        # there is no need to start plain AppVMs automatically
        ret_code_, _ = shutdown_domains(to_shutdown, log)
        ret_code = max(ret_code, ret_code_)

    return ret_code


def get_derived_vm_to_apply(templates, derived_statuses):
    possibly_changed_vms = set()
    for template in templates:
        possibly_changed_vms.update(template.derived_vms)

    to_restart = set()
    to_shutdown = set()

    for vm in possibly_changed_vms:
        if (not bool(derived_statuses.get(vm.name, False))
                and vm.is_running()
                and (vm.klass != 'DispVM' or not vm.auto_cleanup)):
            if get_boolean_feature(vm, 'servicevm', False):
                to_restart.add(vm)
            else:
                to_shutdown.add(vm)

    return to_restart, to_shutdown


def shutdown_domains(to_shutdown, log):
    """
    Try to shut down vms and wait to finish.
    """
    ret_code = EXIT.OK
    wait_for = []
    for vm in to_shutdown:
        try:
            vm.shutdown(force=True)
            wait_for.append(vm)
        except qubesadmin.exc.QubesVMError as exc:
            log.error(str(exc))
            ret_code = EXIT.ERR_SHUTDOWN_APP

    asyncio.run(wait_for_domain_shutdown(wait_for))

    return ret_code, wait_for


def restart_vms(to_restart, log):
    """
    Try to restart vms.
    """
    ret_code, shutdowns = shutdown_domains(to_restart, log)

    # restart shutdown qubes
    for vm in shutdowns:
        try:
            vm.start()
        except qubesadmin.exc.QubesVMError as exc:
            log.error(str(exc))
            ret_code = EXIT.ERR_START_APP

    return ret_code


if __name__ == '__main__':
    sys.exit(main())
