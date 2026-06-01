#!/usr/bin/python3
"""
qvm-template-upgrade — perform an N -> N+1 distro version upgrade of a qube

Workflow:
    1. Validate that --template names an existing TemplateVM or StandaloneVM.
    2. Read os-distribution / os-version from qvm-features.
    3. Compute the target version as os-version + 1 (N -> N+1 is the
       only supported scope; multi-hop is rejected by construction).
    4. Clone the qube to a new name derived from the target version.
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

DATE_FMT = '%Y-%m-%d %H:%M:%S'


class UpgradeError(Exception):
    """Failure during the upgrade run itself."""


class ValidationError(Exception):
    """Invalid user input or unsupported source qube."""


def compute_target_version(current):
    """Return current + 1 as the target distro version.

    Non-integer versions are rejected here.
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

    Examples:
        fedora-41, 41 -> 42  =>  fedora-42

        fedora-41-minimal, 41 -> 42  =>  fedora-42-minimal
    """
    if override:
        return override
    if current_version not in source_name:
        raise ValidationError(
            f"Cannot derive new template name from {source_name!r}: it does "
            f"not contain the current version {current_version!r}. Pass "
            f"--new-name explicitly.")
    # Replace only the last occurrence (e.g. fedora-41-extras-41 stays sane).
    head, _, tail = source_name.rpartition(current_version)
    return f"{head}{target_version}{tail}"

# Argument parsing / logging

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
    # Don't let our messages also flow through the root logger.
    log.propagate = False
    # Idempotent: if main() is called more than once in the same process
    # (embedded use, repeated CLI invocations in tests), skip re-adding
    # handlers so output isn't duplicated.
    if log.handlers:
        return log
    # Always log to stderr: so user sees progress even when the log file
    # is unavailable (dev machine without /var/log/qubes, perms issues, etc.).
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter('%(message)s'))
    log.addHandler(stderr)
    try:
        handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        log.addHandler(handler)
    except OSError as err:
        log.warning("Could not open log file %s: %s", LOG_PATH, err)
    return log


# Orchestrator


class TemplateUpgrader:
    """Stateful orchestrator for one source qube upgrade."""

    def __init__(self, app, args, log):
        self.app = app
        self.args = args
        self.log = log
        # Populated by validate():
        self.source_vm = None
        self.distro = None
        self.current_version = None
        self.target_version = None
        self.new_name = None
        # Populated by clone():
        self.clone_vm = None

    # validation

    def validate(self):
        """Run all pre-flight checks. Populates planning attributes.

        Raises ValidationError on any input/setup problem. After this call,
        self.source_vm / distro / current_version / target_version / new_name
        are all set and the upgrade can proceed (or be reported via
        describe_plan() for --dry-run).
        """
        self.source_vm = self._resolve_source_qube()
        self.distro, self.current_version = self._detect_distro()
        self.target_version = compute_target_version(self.current_version)
        self.new_name = derive_clone_name(
            self.source_vm.name,
            self.current_version,
            self.target_version,
            self.args.new_name,
        )
        if self.new_name in self.app.domains:
            raise ValidationError(
                f"Target name {self.new_name!r} already exists. Remove it "
                f"first or pass a different --new-name.")

    def _resolve_source_qube(self):
        try:
            vm = self.app.domains[self.args.template]
        except KeyError:
            raise ValidationError(f"No such qube: {self.args.template}")
        if vm.klass not in SUPPORTED_CLASSES:
            raise ValidationError(
                f"{vm.name} is a {vm.klass}; only TemplateVMs and "
                f"StandaloneVMs can be upgraded with this tool.")
        return vm

    def _detect_distro(self):
        distro = self.source_vm.features.get('os-distribution')
        distro_like = self.source_vm.features.get('os-distribution-like', '')
        version = self.source_vm.features.get('os-version')
        if not distro or not version:
            raise ValidationError(
                f"{self.source_vm.name} is missing os-distribution / "
                f"os-version features. Start the qube once so the in-VM "
                f"agent can report them, then retry.")
        candidates = {distro.lower(), *distro_like.lower().split()}
        supported = SUPPORTED_DISTROS & candidates
        if not supported:
            raise ValidationError(
                f"Unsupported distro {distro!r}; only Fedora- and "
                f"Debian-based qubes are supported for now.")
        return sorted(supported)[0], version

    def describe_plan(self):
        return (f"upgrade {self.source_vm.name} "
                f"({self.distro} {self.current_version}) -> "
                f"clone {self.new_name} "
                f"({self.distro} {self.target_version})")

    # execution

    def clone(self):
        """Clone the source qube. Populates self.clone_vm."""
        self.log.info("Cloning %s -> %s", self.source_vm.name, self.new_name)
        self.clone_vm = self.app.clone_vm(self.source_vm, self.new_name)

    def run_agent(self):
        """Run the in-VM upgrade agent inside the clone.

        STUB: replaced in a follow-up commit by a dispatch into a new
        `version_upgrade(target_version)` method on the existing
        vmupdate agent (vmupdate/agent/source/{dnf,apt}/), reused via the
        qrexec transport in qube_connection.py. The VM-side agent must
        re-detect or verify the distro from inside the qube before running
        distro-specific upgrade commands.
        """
        raise NotImplementedError(
            f"version-upgrade agent is not implemented yet for "
            f"{self.clone_vm.name} -> {self.target_version}")

    def finalize(self):
        """Write post-upgrade qvm-features on the clone.

        TemplateVM: always set template-name (required so qvm-template
        recognises the upgraded clone as managed) and refresh
        template-installtime.

        StandaloneVM: rewrite an existing template-name from the old to
        the new release (e.g. fedora-41 -> fedora-42), keeping the value
        compatible with qui.utils.check_support()'s EOL_DATES lookup
        (which strips only -minimal / -xfce, not -standalone or the qube
        name). We do not invent a template-name for standalones that
        never had one, and we leave one in place that doesn't carry the
        current version (can't safely transform).

        Inherited EVR/buildtime features are intentionally left in place
        because qvm-template.query_local() uses bracket access on them and
        deleting would crash qvm-template list/info for this qube.
        """
        self.log.info("Updating metadata on %s", self.clone_vm.name)
        if self.clone_vm.klass == 'TemplateVM':
            self.clone_vm.features['template-name'] = self.clone_vm.name
            self.clone_vm.features['template-installtime'] = \
                datetime.now(tz=timezone.utc).strftime(DATE_FMT)
            return
        old = self.clone_vm.features.get('template-name')
        if not old:
            return
        try:
            new = derive_clone_name(
                old, self.current_version, self.target_version, None)
        except ValidationError:
            # template-name doesn't carry the current version (custom
            # value, manual edit) it is safer to leave it alone than to
            # guess what the user intended.
            self.log.info(
                "Leaving standalone template-name=%r untouched "
                "(no version substring to rewrite)", old)
            return
        self.clone_vm.features['template-name'] = new

    def rollback(self):
        """Remove the half-upgraded clone, if any. Safe to call repeatedly."""
        if self.clone_vm is None:
            return
        self.log.warning("Removing failed clone %s", self.clone_vm.name)
        try:
            del self.app.domains[self.clone_vm.name]
        except qubesadmin.exc.QubesException as err:
            self.log.error("Could not remove failed clone %s: %s",
                           self.clone_vm.name, err)


# CLI entry point

def main(argv=None, app=None):
    parser, args = parse_args(argv, app)
    log = setup_logging(args.log)
    upgrader = TemplateUpgrader(args.app, args, log)

    try:
        upgrader.validate()
    except ValidationError as err:
        parser.print_error(str(err))
        return EXIT.ERR_USAGE

    log.info("Plan: %s", upgrader.describe_plan())

    if args.dry_run:
        print(f"[dry-run] would clone {upgrader.source_vm.name} -> "
              f"{upgrader.new_name} and upgrade {upgrader.distro} "
              f"{upgrader.current_version} -> {upgrader.target_version}")
        return EXIT.OK

    try:
        upgrader.clone()
    except qubesadmin.exc.QubesException as err:
        print(f"error: clone failed: {err}", file=sys.stderr)
        return EXIT.ERR

    try:
        upgrader.run_agent()
        upgrader.finalize()
    except (UpgradeError, NotImplementedError,
            qubesadmin.exc.QubesException) as err:
        log.error("Upgrade failed: %s", err)
        if not args.keep_on_failure:
            upgrader.rollback()
        else:
            log.info("Leaving clone %s in place (--keep-on-failure).",
                     upgrader.clone_vm.name)
        print(f"error: {err}", file=sys.stderr)
        return EXIT.ERR

    label = 'template' if upgrader.clone_vm.klass == 'TemplateVM' \
        else 'standalone'
    print(f"Upgrade complete. New {label}: {upgrader.clone_vm.name}")
    print(f"Original qube {upgrader.source_vm.name} is untouched.")
    return EXIT.OK


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
