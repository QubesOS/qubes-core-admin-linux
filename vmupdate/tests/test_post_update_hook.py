# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2026  Andreas Glashauser <ag@andreasglashauser.com>
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
import logging

from vmupdate.agent.source.common.post_update_hook import (
    run_post_update_hook,
)

log = logging.getLogger("test")


def write_hook(tmp_path, body):
    hook = tmp_path / "qubes.PostUpdate"
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)
    return str(hook)


def test_missing_hook_is_not_an_error(tmp_path):
    result = run_post_update_hook(str(tmp_path / "missing"), log)
    assert repr(result) == "0; ; "


def test_hook_output_is_captured(tmp_path):
    hook = write_hook(tmp_path, "echo updated\necho careful >&2\n")
    result = run_post_update_hook(hook, log)
    assert repr(result) == "0; updated\n; careful\n"


def test_failing_hook_maps_to_post_update_error(tmp_path):
    hook = write_hook(tmp_path, "echo boom >&2\nexit 3\n")
    result = run_post_update_hook(hook, log)
    assert repr(result) == "27; ; boom\n"


def test_print_streams_forwards_output(tmp_path, capsys):
    hook = write_hook(tmp_path, "echo updated\necho careful >&2\n")
    run_post_update_hook(hook, log, print_streams=True)
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("updated\n\n", "careful\n\n")
