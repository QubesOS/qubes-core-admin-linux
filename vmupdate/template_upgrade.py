#!/usr/bin/python3
"""
qvm-template-upgrade — perform an N -> N+1 distro version upgrade of a qube

Workflow:
    1. Validate that --template names an existing TemplateVM or StandaloneVM.
    2. Read os-distribution / os-version from qvm-features.
    3. Compute the target version as os-version + 1 (N -> N+1 is the
       only supported scope; multi-hop is rejected by construction).
    4. Clone the template to a new name derived from the target version.
    5. Run the in-VM version-upgrade agent inside the clone
        (reuses the vmupdate qrexec transport — currently stubbed).
    6. On success: update template metadata features on the clone.
    7. On failure: remove the half-upgraded clone unless --keep-on-failure.

The original qube is never touched by this tool. AppVMs based on a source
template continue to use it until the user manually switches them and
uninstalls the old template.
"""
import logging
import sys
from datetime import datetime, timezone

import qubesadmin
import qubesadmin.exc
import qubesadmin.tools

from vmupdate.agent.source.common.exit_codes import EXIT

LOG_PATH = '/var/log/qubes/qvm-template-upgrade.log'
LOG_FORMAT = '%(asctime)s %(levelname)s %(message)s'

SUPPORTED_DISTROS = {'fedora', 'debian'}
SUPPORTED_CLASSES = {'TemplateVM', 'StandaloneVM'}

# Features that describe the upgraded template's identity for qvm-template
# and the Qubes updater. See qvm_template.py:409 (is_managed_by_qvmtemplate)
# — template-name == vm.name is the management check.
DATE_FMT = '%Y-%m-%d %H:%M:%S'


class UpgradeError(Exception):
    """Anything that prevents the upgrade from running or completing."""


class ValidationError(Exception):
    """Invalid user input or unsupported source qube."""


def get_parser():
    parser = qubesadmin.tools.QubesArgumentParser(
        prog='qvm-template-upgrade',
        description='Upgrade a TemplateVM or StandaloneVM to the next distro '
                    'version.',
        version=''
    )
    parser.add_argument(
        '--template', required=True,
        help='Name of the source TemplateVM or StandaloneVM to upgrade.')
    parser.add_argument(
        '--new-name',
        help='Name for the upgraded clone. Defaults to replacing the version '
             'suffix in the source name (e.g. fedora-41 -> fedora-42).')
    parser.add_argument(
        '--keep-on-failure', action='store_true',
        help='Preserve the half-upgraded clone if the upgrade fails. '
             'By default the clone is removed and the original remains.')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Validate inputs and print the planned actions; do not clone '
             'or upgrade anything.')
    parser.add_argument(
        '--log', default='INFO',
        help='Log level (default: INFO).')
    return parser


def parse_args(argv=None, app=None):
    parser = get_parser()
    return parser, parser.parse_args(argv, app=app)


def setup_logging(level):
    log = logging.getLogger('vm-template-upgrade')
    log.setLevel(level)
    handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    log.addHandler(handler)
    # Also mirror to stderr so the user sees progress without tailing the log.
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter('%(message)s'))
    log.addHandler(stderr)
    return log


def validate_template(app, name):
    """Return VM if `name` is an upgradeable qube, else raise."""
    try:
        vm = app.domains[name]
    except KeyError:
        raise ValidationError(f"No such qube: {name}")
    if vm.klass not in SUPPORTED_CLASSES:
        raise ValidationError(
            f"{name} is a {vm.klass}; only TemplateVMs and StandaloneVMs "
            f"can be upgraded with this tool.")
    return vm


def detect_distro(vm):
    """Read distro family and current version from qvm-features."""
    distro = vm.features.get('os-distribution')
    distro_like = vm.features.get('os-distribution-like', '')
    version = vm.features.get('os-version')
    if not distro or not version:
        raise ValidationError(
            f"{vm.name} is missing os-distribution / os-version features. "
            f"Start the template once so the in-VM agent can report them, "
            f"then retry.")
    candidates = {distro.lower(), *distro_like.lower().split()}
    supported = SUPPORTED_DISTROS & candidates
    if not supported:
        raise ValidationError(
            f"Unsupported distro {distro!r}; only Fedora- and Debian-based "
            f"templates are supported for now.")
    return sorted(supported)[0], version


def compute_target_version(current):
    """Return the next version as the target distro version.

    N -> N+1 is the only supported scope for now.
    """
    try:
        current_n = int(current)
    except ValueError:
        raise ValidationError(
            f"Non-numeric distro version {current!r}; multi-component "
            f"versions (e.g. Debian point releases) are not yet supported "
            f"by this tool.")
    return str(current_n + 1)


def derive_clone_name(source_name, current_version, target_version, override):
    """Replace the version suffix in the source name with the target version.
       fedora-41, 41 -> 42  =>  fedora-42
    """
    if override:
        return override
    if current_version not in source_name:
        raise ValidationError(
            f"Cannot derive new template name from {source_name!r}: it does "
            f"not contain the current version {current_version!r}. Pass "
            f"--new-name explicitly.")
    # Replace only the last occurrence
    head, _, tail = source_name.rpartition(current_version)
    return f"{head}{target_version}{tail}"


def clone_template(app, source_vm, new_name, log):
    """Clone the source template. Fails if `new_name` is already in use."""
    if new_name in app.domains:
        raise ValidationError(
            f"Target name {new_name!r} already exists. Remove it first "
            f"or pass a different --new-name.")
    log.info("Cloning %s -> %s", source_vm.name, new_name)
    return app.clone_vm(source_vm, new_name)


def run_upgrade_agent(clone_vm, target_version, log):
    """Run the in-VM upgrade agent inside the clone.

    STUB: in a follow-up commit this will dispatch into a new
    `version_upgrade(target_version)` method on the existing
    vmupdate agent (vmupdate/agent/source/{dnf,apt}/), reusing the
    qrexec transport in qube_connection.py. The VM-side agent must re-detect
    or verify the distro from inside the qube before running distro-specific
    upgrade commands.
    """
    raise NotImplementedError(
        f"version-upgrade agent is not implemented yet for {clone_vm.name} "
        f"-> {target_version}")


def apply_post_upgrade_metadata(clone_vm, log):
    """Update qvm-features on the upgraded clone.

    We set template-name and refresh template-installtime. Inherited
    qvm-template EVR/buildtime features are intentionally preserved because
    current qvm-template list/info paths require them for managed templates.
    """
    if clone_vm.klass != 'TemplateVM':
        return
    log.info("Updating template metadata on %s", clone_vm.name)
    clone_vm.features['template-name'] = clone_vm.name
    clone_vm.features['template-installtime'] = \
        datetime.now(tz=timezone.utc).strftime(DATE_FMT)


def remove_failed_clone(clone_vm, log):
    log.warning("Removing failed clone %s", clone_vm.name)
    try:
        del clone_vm.app.domains[clone_vm.name]
    except qubesadmin.exc.QubesException as err:
        log.error("Could not remove failed clone %s: %s", clone_vm.name, err)


def main(argv=None, app=None):
    parser, args = parse_args(argv, app)
    log = setup_logging(args.log)
    app = args.app

    try:
        source_vm = validate_template(app, args.template)
        distro, current = detect_distro(source_vm)
        target = compute_target_version(current)
        new_name = derive_clone_name(
            source_vm.name, current, target, args.new_name)
    except ValidationError as err:
        parser.print_error(str(err))
        return EXIT.ERR_USAGE

    log.info("Plan: upgrade %s (%s %s) -> clone %s (%s %s)",
             source_vm.name, distro, current, new_name, distro, target)

    if args.dry_run:
        print(f"[dry-run] would clone {source_vm.name} -> {new_name} and "
              f"upgrade {distro} {current} -> {target}")
        return EXIT.OK

    try:
        clone_vm = clone_template(app, source_vm, new_name, log)
    except ValidationError as err:
        parser.print_error(str(err))
        return EXIT.ERR_USAGE
    except qubesadmin.exc.QubesException as err:
        print(f"error: clone failed: {err}", file=sys.stderr)
        return EXIT.ERR

    try:
        if not run_upgrade_agent(clone_vm, target, log):
            raise UpgradeError("upgrade agent reported failure")
        apply_post_upgrade_metadata(clone_vm, log)
    except (UpgradeError, NotImplementedError,
            qubesadmin.exc.QubesException) as err:
        log.error("Upgrade failed: %s", err)
        if not args.keep_on_failure:
            remove_failed_clone(clone_vm, log)
        else:
            log.info("Leaving clone %s in place (--keep-on-failure).",
                     clone_vm.name)
        print(f"error: {err}", file=sys.stderr)
        return EXIT.ERR

    print(f"Upgrade complete. New template: {clone_vm.name}")
    print(f"Original qube {source_vm.name} is untouched.")
    return EXIT.OK


if __name__ == '__main__':
    sys.exit(main())
