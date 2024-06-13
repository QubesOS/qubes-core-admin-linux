# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2024  Piotr Bartman-Szwarc <prbartman@invisiblethingslab.com>
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
import itertools
import queue
from unittest.mock import Mock

import pytest

from vmupdate.agent.source.common.process_result import ProcessResult
from vmupdate.agent.source.status import StatusInfo, FinalStatus


class TestApp:
    class Domains(dict):
        def __iter__(self):
            return iter(self.values())

    def __init__(self):
        self.domains = TestApp.Domains()


class TestVM:
    def __init__(self, name, app, klass, template=None, **kwargs):
        self.name = name
        self.app = app
        self.app.domains[name] = self
        self.klass = klass
        self.running = True
        if self.klass in ('AppVM', 'DispVM'):
            template.derived_vms.append(self)
        self.derived_vms = []
        self.auto_cleanup = False
        self.features = Features(name, app)
        self.shutdown = Mock()
        self.start = Mock()
        for key, value in kwargs.items():
            setattr(self, key, value)

    def is_running(self):
        return self.running

    def __str__(self):
        return self.name

    def __lt__(self, other):
        if isinstance(other, TestVM):
            return self.name < other.name
        return NotImplemented


class Features(dict):
    def __init__(self, qname, app, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qname = qname
        self.app = app

    def check_with_template(self, key, default=None):
        if key in self:
            return self[key]
        for vm in self.app.domains:
            if self.qname in vm.derived_vms:
                return vm.features.get(key, default)
        return default


class MPManager:
    class Value:
        def __init__(self, _type, value):
            self.value = value

    class Queue:
        def __init__(self):
            self._queue = []

        def get(self, block):
            if not self._queue:
                raise queue.Empty
            return self._queue.pop(0)

        def put(self, obj):
            self._queue.append(obj)


@pytest.fixture()
def test_manager():
    return MPManager()


class MPPool(Mock):
    def apply_async(self, func, args, *, callback, **_kwargs):
        callback(func(*args))


@pytest.fixture()
def test_pool():
    return MPPool()


@pytest.fixture
def test_qapp():
    app = TestApp()
    return app


@pytest.fixture()
def test_agent():
    def closure(results, unexpected):
        class UpdateAgentManager:
            def __init__(self, app, qube, agent_args, show_progress):
                self.qube = qube

            def run_agent(self, agent_args, status_notifier, termination):
                if self.qube.name not in results:
                    status_notifier.put(
                        StatusInfo.done(self.qube, FinalStatus.UNKNOWN))
                    unexpected.append(self.qube.name)
                    return ProcessResult(code=99)
                for status in results[self.qube.name]["statuses"]:
                    status_notifier.put(
                        StatusInfo.done(self.qube, status))
                result = ProcessResult(code=results[self.qube.name]["retcode"])
                del results[self.qube.name]
                return result

        return UpdateAgentManager

    return closure


def generate_vm_variations(app, variations):
    """
    Generate all possible variations of vms for the given list of features.
    """
    dom0 = TestVM("dom0", app, klass="AdminVM", updateable=True, running=True,
                  update_result=FinalStatus.UNKNOWN,
                  features=Features("dom0", app, {'updates-available': True}))
    domains = {
        "klass": {"TemplateVM": set(), "StandaloneVM": set(), "AppVM": set(),
                  "DispVM": set()},
        "is_running": {False: set(), True: set()},
        "servicevm": {False: set(), True: set()},
        "auto_cleanup": {False: set(), True: set()},
        "updatable": {True: set(), False: set()},
        "updates_available": {False: set(), True: set()},
        "last_updates_check": {None: set(), '2020-01-01 00:00:00': set(),
                               '3020-01-01 00:00:00': set()},
        "qrexec": {False: set(), True: set()},
        "os": {'Linux': set(), 'BSD': set()},
        "updated": {FinalStatus.UNKNOWN: set(), FinalStatus.SUCCESS: set(),
                    FinalStatus.NO_UPDATES: set(), FinalStatus.ERROR: set(),
                    FinalStatus.CANCELLED: set(),
                    },
        "has_template_updated": {
            FinalStatus.SUCCESS: set(), FinalStatus.NO_UPDATES: set(),
            FinalStatus.ERROR: set(), FinalStatus.CANCELLED: set(),
            FinalStatus.UNKNOWN: set()},
    }

    klasses = list(reversed(sorted(list(domains['klass'].keys()))))
    if "klass" not in variations:
        klasses = klasses[:1]
    rest = [list(domains[key].keys())
            if key in variations else list(domains[key].keys())[:1]
            for key in domains.keys() if key != "klass"]
    for k in klasses:
        for (running, servicevm, auto_cleanup, updatable, updates_available,
             last_check, qrexec, os, updated, template_updated
             ) in itertools.product(*rest):

            if not updatable and (updates_available or last_check):
                # do not consider features about updates for non-updatable vms
                continue
            if auto_cleanup and k != "DispVM":
                # `auto_cleanup` is applicable only to DispVM
                continue
            if (os or qrexec) and updates_available:
                # if `updates_available` we never use qrexec or check os
                continue
            if updated != FinalStatus.UNKNOWN and k not in ("DispVM", "AppVM"):
                # result of updating for templates and standalones bases on
                # `template_updated`
                continue

            lc_enc = {None: '0', '2020-01-01 00:00:00': '1',
                              '3020-01-01 00:00:00': '2'}
            os_enc = {'Linux': '0', 'BSD': '1'}
            f_map = {FinalStatus.SUCCESS: "0", FinalStatus.ERROR: "1",
                     FinalStatus.CANCELLED: "2", FinalStatus.NO_UPDATES: "3",
                     FinalStatus.UNKNOWN: "4"}
            txt = lambda x: str(int(x))
            suffix = (txt(running) + txt(servicevm) + lc_enc[last_check] +
                      txt(updates_available) + txt(qrexec) + os_enc[os] +
                      txt(updatable) + txt(auto_cleanup))
            if k in ('DispVM', 'AppVM'):
                template = app.domains[
                    'T' + f_map[template_updated] + "4" + suffix[:-1] + "0"]
                ext_suffix = f_map[updated] + f_map[template_updated] + suffix
                update_result = updated
            else:
                template = None
                ext_suffix = f_map[template_updated] + "4" + suffix
                update_result = template_updated

            features = {}
            if servicevm:
                features['servicevm'] = True
            if updates_available:
                features['updates-available'] = True
            if last_check:
                features['last-updates-check'] = last_check
            if qrexec:
                features['qrexec'] = qrexec
            if os:
                features['os'] = os

            vm = TestVM(
                k[0] + ext_suffix, app, klass=k, updateable=updatable,
                running=running, auto_cleanup=auto_cleanup, template=template,
                features=Features(k[0] + ext_suffix, app, features),
                update_result=update_result)

            domains["klass"][k].add(vm)
            domains["is_running"][running].add(vm)
            domains["servicevm"][servicevm].add(vm)
            domains["auto_cleanup"][auto_cleanup].add(vm)
            domains["updatable"][updatable].add(vm)
            domains["updates_available"][updates_available].add(vm)
            domains["last_updates_check"][last_check].add(vm)
            domains["qrexec"][qrexec].add(vm)
            domains["os"][os].add(vm)
            if k in ('DispVM', 'AppVM'):
                domains["updated"][updated].add(vm)
                domains["has_template_updated"][template_updated].add(vm)
            else:
                domains["updated"][template_updated].add(vm)
                domains["has_template_updated"][updated].add(vm)

    domains["klass"]["AdminVM"] = {dom0}
    dom_prop = {
        "is_running": True, "servicevm": False, "auto_cleanup": False,
        "updatable": True, "updates_available": True,
        "last_updates_check": None, "updated": FinalStatus.UNKNOWN,
        "has_template_updated": FinalStatus.UNKNOWN}
    for key, subkey in dom_prop.items():
        try:
            domains[key][subkey].add(dom0)
        except KeyError:
            pass

    return domains
