====================
qvm-template-upgrade
====================

NAME
====
qvm-template-upgrade - upgrade a clone to the next distribution release

SYNOPSIS
========
| qvm-template-upgrade --template=<NAME> [OPTIONS]

DESCRIPTION
===========
Clone the source qube and run the distribution release upgrade inside the
clone. Agent output is streamed to the terminal above a progress bar and
written to the log files listed in FILES. The progress bar is skipped when
stderr is not a terminal.

OPTIONS
=======

--template=<NAME>
    Name of the source TemplateVM or StandaloneVM. This option is required.
--new-name=<NAME>
    Name for the clone. By default, replace the final ``os-version`` in the source name.
--keep-new-on-failure
    Keep the clone if the in-qube upgrade fails so that it can be inspected.
--dry-run
    Validate inputs and print the plan without creating a clone.
--log=<LEVEL>
    Set the workflow and in-qube agent log level.
    Accepted values (case-insensitive) are ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``, and ``CRITICAL``.
-v, --verbose
    Increase qubesadmin client verbosity.
-q, --quiet
    Decrease qubesadmin client verbosity.
-h, --help
    Show this help message and exit.

CLONE NAME DERIVATION
=====================

Unless ``--new-name`` is set, replace the final current version. Append the
target version when it is absent:

- ``fedora-41`` becomes ``fedora-42``.
- ``fedora-41-minimal`` becomes ``fedora-42-minimal``.
- ``custom-vm`` becomes ``custom-vm-42``.

TEMPLATE METADATA
=================

After a TemplateVM upgrade, update these ``template-*`` features:

- ``template-name`` is set to the clone's name.
- ``template-installtime`` and ``template-buildtime`` are set to the time of the upgrade, since the clone's root filesystem was produced then.
- ``template-reponame`` is set to ``@qvm-template-upgrade``, marking the qube as neither installed from a repository nor from a local package.
- ``template-summary`` and ``template-description`` have every occurrence of the source qube's name replaced with the clone's. Any other text is user-authored and is left alone.

``template-epoch``, ``template-version``, and ``template-release`` stay
inherited. **qvm-template**\ (1) uses them for package comparisons.

StandaloneVMs are not managed by **qvm-template**\ (1), so none of their ``template-*`` features are modified.

The inherited version makes **qvm-template**\ (1) list the clone as
upgradeable. ``qvm-template upgrade`` would replace it with a stock template.

RETURN CODES
============

0:   The upgrade or dry run completed. A metadata-write warning after an otherwise successful upgrade also returns this status.

1:   Clone creation, qrexec transport, or the in-qube upgrade failed.

2:   Command-line parsing error.

64:  Dom0 preflight validation error, such as an unsupported source qube, a derivative distribution with its own version numbering, missing distribution features, a non-integer version, or an existing target name.

130: Interrupted by the user. The clone may remain.

FILES
=====

``/var/log/qubes/qvm-template-upgrade.log``
    Main workflow log. If the file cannot be opened, the command continues with stderr logging only.

``/var/log/qubes/update-<CLONE_NAME>.log``
    Detailed qrexec transport and in-qube agent log. This file is created after the agent starts.

SEE ALSO
========

**qubes-vm-update**\ (1), **qvm-clone**\ (1), **qvm-features**\ (1), **qvm-template**\ (1)

AUTHORS
=======
This command was written by Nihal Kumar <nihalxkumar at tutamail dot com> as part of Google Summer of Code.

Ben Grande <ben at invisiblethingslab dot com> reviewed the change.

It was inspired by a personal project by Kenneth R. Rosen <kennethrrosen at proton dot me>.
