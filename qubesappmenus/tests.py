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

import colorsys
import os

import unittest
import pkg_resources
import xdg
import xdg.BaseDirectory
import xdg.DesktopEntry
import qubes
import qubes.tests
import qubes.tests.extra
import qubes.vm.appvm
import qubes.vm.templatevm
import qubesappmenus
import qubesappmenus.receive
import qubesimgconverter


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

    def test_100_get_appmenus(self):
        def _run(cmd, **kwargs):
            class PopenMockup(object):
                pass
            self.assertEquals(cmd, 'QUBESRPC qubes.GetAppmenus dom0')
            self.assertEquals(kwargs.get('passio_popen', False), True)
            self.assertEquals(kwargs.get('gui', True), False)
            p = PopenMockup()
            p.stdout = pkg_resources.resource_stream(__name__,
                'test-data/appmenus.input')
            p.wait = lambda: None
            p.returncode = 0
            return p
        vm = TestVM('test-vm', run=_run)
        appmenus = qubesappmenus.receive.get_appmenus(vm)
        expected_appmenus = {
            'org.gnome.Nautilus.desktop': {
                'Name': 'Files',
                'Comment': 'Access and organize files',
                'Categories': 'GNOME;GTK;Utility;Core;FileManager;',
                'Exec': 'qubes-desktop-run '
                        '/usr/share/applications/org.gnome.Nautilus.desktop',
                'Icon': 'system-file-manager',
            },
            'org.gnome.Weather.Application.desktop': {
                'Name': 'Weather',
                'Comment': 'Show weather conditions and forecast',
                'Categories': 'GNOME;GTK;Utility;Core;',
                'Exec': 'qubes-desktop-run '
                        '/usr/share/applications/org.gnome.Weather.Application.desktop',
                'Icon': 'org.gnome.Weather.Application',
            },
            'org.gnome.Cheese.desktop': {
                'Name': 'Cheese',
                'GenericName': 'Webcam Booth',
                'Comment': 'Take photos and videos with your webcam, with fun graphical effects',
                'Categories': 'GNOME;AudioVideo;Video;Recorder;',
                'Exec': 'qubes-desktop-run '
                        '/usr/share/applications/org.gnome.Cheese.desktop',
                'Icon': 'cheese',
            },
            'evince.desktop': {
                'Name': 'Document Viewer',
                'Comment': 'View multi-page documents',
                'Categories': 'GNOME;GTK;Office;Viewer;Graphics;2DGraphics;VectorGraphics;',
                'Exec': 'qubes-desktop-run '
                        '/usr/share/applications/evince.desktop',
                'Icon': 'evince',
            },
        }
        self.assertEquals(expected_appmenus, appmenus)


class TC_10_AppmenusIntegration(qubes.tests.extra.ExtraTestCase):
    def setUp(self):
        super(TC_10_AppmenusIntegration, self).setUp()
        self.vm = self.create_vms(['vm'])[0]
        self.appmenus = qubesappmenus.AppmenusExtension()

    def assertPathExists(self, path):
        if not os.path.exists(path):
            self.fail("Path {} does not exist".format(path))

    def assertPathNotExists(self, path):
        if os.path.exists(path):
            self.fail("Path {} exists while it should not".format(path))

    def get_whitelist(self, whitelist_path):
        self.assertPathExists(whitelist_path)
        with open(whitelist_path) as f:
            whitelisted = [x.rstrip() for x in f.readlines()]
        return whitelisted

    def test_000_created(self, vm=None):
        if vm is None:
            vm = self.vm
        whitelist_path = os.path.join(vm.dir_path,
            qubesappmenus.AppmenusSubdirs.whitelist)
        whitelisted = self.get_whitelist(whitelist_path)
        self.assertPathExists(self.appmenus.appmenus_dir(vm))
        appmenus = os.listdir(self.appmenus.appmenus_dir(vm))
        self.assertTrue(all(x.startswith(vm.name + '-') for x in appmenus))
        appmenus = [x[len(vm.name) + 1:] for x in appmenus]
        self.assertIn('vm.directory', appmenus)
        appmenus.remove('vm.directory')
        self.assertIn('qubes-appmenu-select.desktop', appmenus)
        appmenus.remove('qubes-appmenu-select.desktop')
        self.assertEquals(set(whitelisted), set(appmenus))
        self.assertPathExists(self.appmenus.icons_dir(vm))
        appicons = os.listdir(self.appmenus.icons_dir(vm))
        whitelisted_icons = set()
        for appmenu in whitelisted:
            desktop = xdg.DesktopEntry.DesktopEntry(
                os.path.join(self.appmenus.appmenus_dir(vm),
                    '-'.join((vm.name, appmenu))))
            if desktop.getIcon():
                whitelisted_icons.add(os.path.basename(desktop.getIcon()))
        self.assertEquals(set(whitelisted_icons), set(appicons))

    def test_001_created_registered(self):
        """Check whether appmenus was registered in desktop environment"""
        whitelist_path = os.path.join(self.vm.dir_path,
            qubesappmenus.AppmenusSubdirs.whitelist)
        if not os.path.exists(whitelist_path):
            self.skipTest("Appmenus whitelist does not exists")
        whitelisted = self.get_whitelist(whitelist_path)
        for appmenu in whitelisted:
            if appmenu.endswith('.directory'):
                subdir = 'desktop-directories'
            else:
                subdir = 'applications'
            self.assertPathExists(os.path.join(
                xdg.BaseDirectory.xdg_data_home, subdir,
                '-'.join([self.vm.name, appmenu])))
        # TODO: some KDE specific dir?

    def test_002_unregistered_after_remove(self):
        """Check whether appmenus was unregistered after VM removal"""
        whitelist_path = os.path.join(self.vm.dir_path,
            qubesappmenus.AppmenusSubdirs.whitelist)
        if not os.path.exists(whitelist_path):
            self.skipTest("Appmenus whitelist does not exists")
        whitelisted = self.get_whitelist(whitelist_path)
        self.vm.remove_from_disk()
        for appmenu in whitelisted:
            if appmenu.endswith('.directory'):
                subdir = 'desktop-directories'
            else:
                subdir = 'applications'
            self.assertPathNotExists(os.path.join(
                xdg.BaseDirectory.xdg_data_home, subdir,
                '-'.join([self.vm.name, appmenu])))

    def test_003_created_template_empty(self):
        tpl = self.app.add_new_vm(qubes.vm.templatevm.TemplateVM,
            name=self.make_vm_name('tpl'), label='red')
        tpl.create_on_disk()
        self.assertPathExists(self.appmenus.templates_dir(tpl))
        self.assertPathExists(self.appmenus.template_icons_dir(tpl))

    def test_004_created_template_from_other(self):
        tpl = self.app.add_new_vm(qubes.vm.templatevm.TemplateVM,
            name=self.make_vm_name('tpl'), label='red')
        tpl.clone_disk_files(self.app.default_template)
        self.assertPathExists(self.appmenus.templates_dir(tpl))
        self.assertPathExists(self.appmenus.template_icons_dir(tpl))
        self.assertPathExists(os.path.join(tpl.dir_path,
            qubesappmenus.AppmenusSubdirs.whitelist))

        for appmenu in os.listdir(self.appmenus.templates_dir(
                self.app.default_template)):
            self.assertPathExists(os.path.join(
                self.appmenus.templates_dir(tpl), appmenu))

        for appicon in os.listdir(self.appmenus.template_icons_dir(
                self.app.default_template)):
            self.assertPathExists(os.path.join(
                self.appmenus.template_icons_dir(tpl), appicon))

    def get_image_color(self, path, expected_color):
        """Return mean color of the image as (r, g, b) in float"""
        image = qubesimgconverter.Image.load_from_file(path)
        _, l, _ = colorsys.rgb_to_hls(
            *qubesimgconverter.hex_to_float(expected_color))

        def get_hls(pixels, l):
            for i in xrange(0, len(pixels), 4):
                r, g, b, a = tuple(ord(c) / 255. for c in pixels[i:i + 4])
                if a == 0.0:
                    continue
                h, _, s = colorsys.rgb_to_hls(r, g, b)
                yield h, l, s

        mean_hls = reduce(
            lambda x, y: (x[0] + y[0], x[1] + y[1], x[2] + y[2]),
            get_hls(image.data, l),
            (0, 0, 0)
        )
        mean_hls = map(lambda x: x / (mean_hls[1] / l), mean_hls)
        image_color = colorsys.hls_to_rgb(*mean_hls)
        return image_color

    def assertIconColor(self, path, expected_color):
        image_color_float = self.get_image_color(path, expected_color)
        expected_color_float = qubesimgconverter.hex_to_float(expected_color)
        if not all(map(lambda a, b: abs(a - b) <= 0.15,
                image_color_float, expected_color_float)):
            self.fail(
                "Icon {} is not colored as {}".format(path, expected_color))

    def test_010_icon_color(self, vm=None):
        if vm is None:
            vm = self.vm
        icons_dir = self.appmenus.icons_dir(vm)
        appicons = os.listdir(icons_dir)
        for icon in appicons:
            self.assertIconColor(os.path.join(icons_dir, icon),
                vm.label.color)

    def test_011_icon_color_label_change(self):
        """Regression test for #1606"""
        self.vm.label = 'green'
        self.test_010_icon_color()

    def test_020_clone(self):
        vm2 = self.app.add_new_vm(qubes.vm.appvm.AppVM,
            name=self.make_vm_name('vm2'), label='green')

        vm2.clone_properties(self.vm)
        vm2.clone_disk_files(self.vm)
        self.test_000_created(vm=vm2)
        self.test_010_icon_color(vm=vm2)


def list_tests():
    return (
        TC_00_Appmenus,
        TC_10_AppmenusIntegration,
    )
