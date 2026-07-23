====================
qvm-template-upgrade
====================

NAME
====
qvm-template-upgrade - clone a qube and upgrade the clone to the next distribution release

SYNOPSIS
========
| qvm-template-upgrade --template=<NAME> [OPTIONS]

OPTIONS
=======

--template=<NAME>
    Name of the source TemplateVM or StandaloneVM. This option is required.
--new-name=<NAME>
    Name for the clone. By default, replaces the last occurrence of the current ``os-version`` in the source name.
--keep-new-on-failure
    Keep the clone if the in-qube upgrade fails so that it can be inspected.
--dry-run
    Run dom0 preflight validation and print the planned source, clone name, distribution, and target release without creating a clone.
--log=<LEVEL>
    Set the workflow and in-qube agent log level.
    Accepted values are ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``, and ``CRITICAL``.
-v, --verbose
    Increase qubesadmin client verbosity.
-q, --quiet
    Decrease qubesadmin client verbosity.
-h, --help
    Show this help message and exit.

CLONE NAME DERIVATION
=====================

Unless ``--new-name`` is specified, the last occurrence of the current version in the source name is replaced with the target version. If the current version is absent, the target version is appended:

- ``fedora-41`` becomes ``fedora-42``.
- ``fedora-41-minimal`` becomes ``fedora-42-minimal``.
- ``custom-vm`` becomes ``custom-vm-42``.

RETURN CODES
============

0:   The upgrade or dry run completed. A metadata-write warning after an otherwise successful upgrade also returns this status.

1:   Clone creation, qrexec transport, or the in-qube upgrade failed.

2:   Command-line parsing error.

64:  Dom0 preflight validation error, such as an unsupported source qube, missing distribution features, a non-integer version, or an existing target name.

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
| Nihal Kumar <nihalxkumar at tutamail dot com>
| Ben Grande <ben at invisiblethingslab dot com>