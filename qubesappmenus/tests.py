#!/usr/bin/python2
# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2016  Marek Marczykowski-Górecki
#                               <marmarek@invisiblethingslab.com>
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
import os

import unittest
import qubes
import qubes.tests
import qubesappmenus


class TestApp(object):
    labels = {1: qubes.Label(1, '0xcc0000', 'red')}

    def __init__(self):
        self.domains = {}


class TestVM(object):
    # pylint: disable=too-few-public-methods
    app = TestApp()

    def __init__(self, name, **kwargs):
        self.running = False
        self.installed_by_rpm = False
        self.is_template = False
        self.name = name
        for k, v in kwargs.items():
            setattr(self, k, v)

    def is_running(self):
        return self.running


class TC_00_Appmenus(qubes.tests.QubesTestCase):
    """Unittests for appmenus, theoretically runnable from git checkout"""
    def setUp(self):
        super(TC_00_Appmenus, self).setUp()
        vmname = qubes.tests.VMPREFIX + 'standalone'
        self.standalone = TestVM(
            name=vmname,
            dir_path=os.path.join(qubes.config.qubes_base_dir, 'appvms',
                vmname),
            updateable=True,
        )
        vmname = qubes.tests.VMPREFIX + 'template'
        self.template = TestVM(
            name=vmname,
            dir_path=os.path.join(
                qubes.config.qubes_base_dir,
                'vm-templates', vmname),
            is_template=True,
            updateable=True,
        )
        vmname = qubes.tests.VMPREFIX + 'vm'
        self.appvm = TestVM(
            name=vmname,
            dir_path=os.path.join(
                qubes.config.qubes_base_dir,
                'appvms', vmname),
            template=self.template,
            updateable=False,
        )
        self.app = TestApp()
        self.ext = qubesappmenus.AppmenusExtension()

    def test_000_templates_dir(self):
        self.assertEquals(
            self.ext.templates_dir(self.standalone),
            os.path.join(qubes.config.qubes_base_dir, 'appvms',
                self.standalone.name, 'apps.templates')
        )
        self.assertEquals(
            self.ext.templates_dir(self.template),
            os.path.join(qubes.config.qubes_base_dir, 'vm-templates',
                self.template.name, 'apps.templates')
        )
        self.assertEquals(
            self.ext.templates_dir(self.appvm),
            os.path.join(qubes.config.qubes_base_dir, 'vm-templates',
                self.template.name, 'apps.templates')
        )

    def test_001_template_icons_dir(self):
        self.assertEquals(
            self.ext.template_icons_dir(self.standalone),
            os.path.join(qubes.config.qubes_base_dir, 'appvms',
                self.standalone.name, 'apps.tempicons')
        )
        self.assertEquals(
            self.ext.template_icons_dir(self.template),
            os.path.join(qubes.config.qubes_base_dir, 'vm-templates',
                self.template.name, 'apps.tempicons')
        )
        self.assertEquals(
            self.ext.template_icons_dir(self.appvm),
            os.path.join(qubes.config.qubes_base_dir, 'vm-templates',
                self.template.name, 'apps.tempicons')
        )

    def test_002_appmenus_dir(self):
        self.assertEquals(
            self.ext.appmenus_dir(self.standalone),
            os.path.join(qubes.config.qubes_base_dir, 'appvms',
                self.standalone.name, 'apps')
        )
        self.assertEquals(
            self.ext.appmenus_dir(self.template),
            os.path.join(qubes.config.qubes_base_dir, 'vm-templates',
                self.template.name, 'apps')
        )
        self.assertEquals(
            self.ext.appmenus_dir(self.appvm),
            os.path.join(qubes.config.qubes_base_dir, 'appvms',
                self.appvm.name, 'apps')
        )

    def test_003_icons_dir(self):
        self.assertEquals(
            self.ext.icons_dir(self.standalone),
            os.path.join(qubes.config.qubes_base_dir, 'appvms',
                self.standalone.name, 'apps.icons')
        )
        self.assertEquals(
            self.ext.icons_dir(self.template),
            os.path.join(qubes.config.qubes_base_dir, 'vm-templates',
                self.template.name, 'apps.icons')
        )
        self.assertEquals(
            self.ext.icons_dir(self.appvm),
            os.path.join(qubes.config.qubes_base_dir, 'appvms',
                self.appvm.name, 'apps.icons')
        )

def list_tests():
    return (
        TC_00_Appmenus,
    )
