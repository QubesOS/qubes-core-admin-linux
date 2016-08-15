#!/usr/bin/python2
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2010  Joanna Rutkowska <joanna@invisiblethingslab.com>
# Copyright (C) 2013  Marek Marczykowski <marmarek@invisiblethingslab.com>
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

import subprocess
import sys
import os
import os.path
import shutil
import dbus
import pkg_resources

import qubes.ext
import qubes.vm.dispvm

import qubesimgconverter


class AppmenusSubdirs:
    templates_subdir = 'apps.templates'
    template_icons_subdir = 'apps.tempicons'
    subdir = 'apps'
    icons_subdir = 'apps.icons'
    template_templates_subdir = 'apps-template.templates'
    whitelist = 'whitelisted-appmenus.list'

    
class AppmenusPaths:
    appmenu_start_hvm_template = \
        '/usr/share/qubes-appmenus/qubes-start.desktop'


class AppmenusExtension(qubes.ext.Extension):
    def __init__(self, *args):
        super(AppmenusExtension, self).__init__(*args)
        import qubes.vm.qubesvm
        import qubes.vm.templatevm

    def templates_dir(self, vm):
        """

        :type vm: qubes.vm.qubesvm.QubesVM
        """
        if vm.updateable:
            return os.path.join(vm.dir_path,
                AppmenusSubdirs.templates_subdir)
        elif hasattr(vm, 'template'):
            return self.templates_dir(vm.template)
        else:
            return None

    def template_icons_dir(self, vm):
        if vm.updateable:
            return os.path.join(vm.dir_path,
                AppmenusSubdirs.template_icons_subdir)
        elif hasattr(vm, 'template'):
            return self.template_icons_dir(vm.template)
        else:
            return None

    def appmenus_dir(self, vm):
        return os.path.join(vm.dir_path, AppmenusSubdirs.subdir)

    def icons_dir(self, vm):
        return os.path.join(vm.dir_path, AppmenusSubdirs.icons_subdir)

    def whitelist_path(self, vm):
        return os.path.join(vm.dir_path, AppmenusSubdirs.whitelist)

    def directory_template_name(self, vm):
        if isinstance(vm, qubes.vm.templatevm.TemplateVM):
            return 'qubes-templatevm.directory.template'
        elif vm.provides_network:
            return 'qubes-servicevm.directory.template'
        else:
            return 'qubes-vm.directory.template'

    def write_desktop_file(self, vm, source, destination_path):
        """Format .desktop/.directory file

        :param vm: QubesVM object for which write desktop file
        :param source: desktop file template (path or template itself)
        :param destination_path: where to write the desktop file
        :return: True if target file was changed, otherwise False
        """
        if source.startswith('/'):
            source = open(source).read()
        data = source.\
            replace("%VMNAME%", vm.name).\
            replace("%VMDIR%", vm.dir_path).\
            replace("%XDGICON%", vm.label.icon)
        if os.path.exists(destination_path):
            current_dest = open(destination_path).read()
            if current_dest == data:
                return False
        with open(destination_path, "w") as f:
            f.write(data)
        return True

    def appmenus_create(self, vm, refresh_cache=True):
        """Create/update .desktop files

        :param vm: QubesVM object for which create entries
        :param refresh_cache: refresh desktop environment cache; if false,
        must be refreshed manually later
        :return: None
        """

        if vm.internal:
            return
        if isinstance(vm, qubes.vm.dispvm.DispVM):
            return

        vm.log.info("Creating appmenus")
        appmenus_dir = self.appmenus_dir(vm)
        if not os.path.exists(appmenus_dir):
            os.makedirs(appmenus_dir)

        anything_changed = False
        directory_file = os.path.join(appmenus_dir, vm.name + '-vm.directory')
        if self.write_desktop_file(vm,
                pkg_resources.resource_string(__name__,
                    self.directory_template_name(vm)), directory_file):
            anything_changed = True

        templates_dir = self.templates_dir(vm)
        if os.path.exists(templates_dir):
            appmenus = os.listdir(templates_dir)
        else:
            appmenus = []
        changed_appmenus = []
        if os.path.exists(self.whitelist_path(vm)):
            whitelist = [x.rstrip() for x in open(self.whitelist_path(vm))]
            appmenus = [x for x in appmenus if x in whitelist]

        for appmenu in appmenus:
            if self.write_desktop_file(vm,
                    os.path.join(templates_dir, appmenu),
                    os.path.join(appmenus_dir,
                        '-'.join((vm.name, appmenu)))):
                changed_appmenus.append(appmenu)
        if self.write_desktop_file(vm,
                pkg_resources.resource_string(
                    __name__, 'qubes-appmenu-select.desktop.template'
                ),
                os.path.join(appmenus_dir,
                    '-'.join((vm.name, 'qubes-appmenu-select.desktop')))):
            changed_appmenus.append('qubes-appmenu-select.desktop')

        if changed_appmenus:
            anything_changed = True

        target_appmenus = map(
            lambda x: '-'.join((vm.name, x)),
            appmenus + ['qubes-appmenu-select.desktop']
        )

        # remove old entries
        installed_appmenus = os.listdir(appmenus_dir)
        installed_appmenus.remove(os.path.basename(directory_file))
        appmenus_to_remove = set(installed_appmenus).difference(set(
            target_appmenus))
        if len(appmenus_to_remove):
            appmenus_to_remove_fnames = map(
                lambda x: os.path.join(appmenus_dir, x), appmenus_to_remove)
            try:
                desktop_menu_cmd = ['xdg-desktop-menu', 'uninstall']
                if not refresh_cache:
                    desktop_menu_cmd.append('--noupdate')
                desktop_menu_cmd.append(directory_file)
                desktop_menu_cmd.extend(appmenus_to_remove_fnames)
                desktop_menu_env = os.environ.copy()
                desktop_menu_env['LC_COLLATE'] = 'C'
                subprocess.check_call(desktop_menu_cmd, env=desktop_menu_env)
            except subprocess.CalledProcessError:
                vm.log.warning("Problem removing old appmenus")

            for appmenu in appmenus_to_remove_fnames:
                os.unlink(appmenu)

        # add new entries
        if anything_changed:
            try:
                desktop_menu_cmd = ['xdg-desktop-menu', 'install']
                if not refresh_cache:
                    desktop_menu_cmd.append('--noupdate')
                desktop_menu_cmd.append(directory_file)
                desktop_menu_cmd.extend(map(
                    lambda x: os.path.join(
                        appmenus_dir, '-'.join((vm.name, x))),
                    changed_appmenus))
                desktop_menu_env = os.environ.copy()
                desktop_menu_env['LC_COLLATE'] = 'C'
                subprocess.check_call(desktop_menu_cmd, env=desktop_menu_env)
            except subprocess.CalledProcessError:
                vm.log.warning("Problem creating appmenus")

        if refresh_cache:
            if 'KDE_SESSION_UID' in os.environ:
                subprocess.call(['kbuildsycoca' +
                                 os.environ.get('KDE_SESSION_VERSION', '4')])

    def appmenus_remove(self, vm, refresh_cache=True):
        appmenus_dir = self.appmenus_dir(vm)
        if os.path.exists(appmenus_dir):
            vm.log.info("Removing appmenus")
            installed_appmenus = os.listdir(appmenus_dir)
            directory_file = os.path.join(self.appmenus_dir(vm),
                vm.name + '-vm.directory')
            installed_appmenus.remove(os.path.basename(directory_file))
            if installed_appmenus:
                appmenus_to_remove_fnames = map(
                    lambda x: os.path.join(appmenus_dir, x), installed_appmenus)
                try:
                    desktop_menu_cmd = ['xdg-desktop-menu', 'uninstall']
                    if not refresh_cache:
                        desktop_menu_cmd.append('--noupdate')
                    desktop_menu_cmd.append(directory_file)
                    desktop_menu_cmd.extend(appmenus_to_remove_fnames)
                    desktop_menu_env = os.environ.copy()
                    desktop_menu_env['LC_COLLATE'] = 'C'
                    subprocess.check_call(desktop_menu_cmd,
                        env=desktop_menu_env)
                except subprocess.CalledProcessError:
                    vm.log.warning("Problem removing appmenus")
            shutil.rmtree(appmenus_dir)

        if refresh_cache:
            if 'KDE_SESSION_UID' in os.environ:
                subprocess.call(['kbuildsycoca' +
                                 os.environ.get('KDE_SESSION_VERSION', '4')])

    def appicons_create(self, vm, srcdir=None, force=False):
        """Create/update applications icons"""
        if srcdir is None:
            srcdir = self.template_icons_dir(vm)
        if srcdir is None:
            return
        if not os.path.exists(srcdir):
            return

        if vm.internal:
            return
        if isinstance(vm, qubes.vm.dispvm.DispVM):
            return

        whitelist = self.whitelist_path(vm)
        if os.path.exists(whitelist):
            whitelist = [line.strip() for line in open(whitelist)]
        else:
            whitelist = None

        dstdir = self.icons_dir(vm)
        if not os.path.exists(dstdir):
            os.mkdir(dstdir)
        elif not os.path.isdir(dstdir):
            os.unlink(dstdir)
            os.mkdir(dstdir)

        if whitelist:
            expected_icons = \
                map(lambda x: os.path.splitext(x)[0] + '.png', whitelist)
        else:
            expected_icons = os.listdir(srcdir)

        for icon in os.listdir(srcdir):
            if icon not in expected_icons:
                continue

            src_icon = os.path.join(srcdir, icon)
            dst_icon = os.path.join(dstdir, icon)
            if not os.path.exists(dst_icon) or force or \
                    os.path.getmtime(src_icon) > os.path.getmtime(dst_icon):
                qubesimgconverter.tint(src_icon, dst_icon, vm.label.color)

        for icon in os.listdir(dstdir):
            if icon not in expected_icons:
                os.unlink(os.path.join(dstdir, icon))

    def appicons_remove(self, vm):
        if not os.path.exists(self.icons_dir(vm)):
            return
        shutil.rmtree(self.icons_dir(vm))

    @qubes.ext.handler('property-pre-set:name', vm=qubes.vm.qubesvm.QubesVM)
    def pre_rename(self, vm, event, prop, *args):
        if not os.path.exists(vm.dir_path):
            return
        self.appmenus_remove(vm)

    @qubes.ext.handler('property-set:name', vm=qubes.vm.qubesvm.QubesVM)
    def post_rename(self, vm, event, prop, *args):
        if not os.path.exists(vm.dir_path):
            return
        self.appmenus_create(vm)

    @qubes.ext.handler('domain-create-on-disk')
    def create_on_disk(self, vm, event):
        try:
            source_template = vm.template
        except AttributeError:
            source_template = None
        if vm.updateable and source_template is None:
            os.mkdir(self.templates_dir(vm))
            os.mkdir(self.template_icons_dir(vm))
        if vm.hvm and source_template is None:
            vm.log.info("Creating appmenus directory: {0}".format(
                self.templates_dir(vm)))
            shutil.copy(AppmenusPaths.appmenu_start_hvm_template,
                        self.templates_dir(vm))

        source_whitelist_filename = 'vm-' + AppmenusSubdirs.whitelist
        if source_template and os.path.exists(
                os.path.join(source_template.dir_path, source_whitelist_filename)):
            vm.log.info("Creating default whitelisted apps list: {0}".
                    format(vm.dir_path + '/' + AppmenusSubdirs.whitelist))
            shutil.copy(
                os.path.join(source_template.dir_path, source_whitelist_filename),
                os.path.join(vm.dir_path, AppmenusSubdirs.whitelist))

        if vm.updateable:
            vm.log.info("Creating/copying appmenus templates")
            if source_template and os.path.isdir(self.templates_dir(
                    source_template)):
                shutil.copytree(self.templates_dir(source_template),
                                self.templates_dir(vm))
            if source_template and os.path.isdir(self.template_icons_dir(
                    source_template)):
                shutil.copytree(self.template_icons_dir(source_template),
                                self.template_icons_dir(vm))

        # Create appmenus
        self.appicons_create(vm)
        self.appmenus_create(vm)

    @qubes.ext.handler('domain-clone-files')
    def clone_disk_files(self, vm, event, src_vm):
        if src_vm.updateable and self.templates_dir(vm) is not None and \
                self.templates_dir(vm) is not None:
            vm.log.info("Copying the template's appmenus templates "
                        "dir:\n{0} ==>\n{1}".
                    format(self.templates_dir(src_vm),
                           self.templates_dir(vm)))
            shutil.copytree(self.templates_dir(src_vm),
                            self.templates_dir(vm))

        if src_vm.updateable and self.template_icons_dir(vm) is not None \
                and self.template_icons_dir(vm) is not None and \
                os.path.isdir(self.template_icons_dir(src_vm)):
            vm.log.info("Copying the template's appmenus "
                        "template icons dir:\n{0} ==>\n{1}".
                    format(self.template_icons_dir(src_vm),
                           self.template_icons_dir(vm)))
            shutil.copytree(self.template_icons_dir(src_vm),
                            self.template_icons_dir(vm))

        for whitelist in (
                AppmenusSubdirs.whitelist,
                'vm-' + AppmenusSubdirs.whitelist,
                'netvm-' + AppmenusSubdirs.whitelist):
            if os.path.exists(os.path.join(src_vm.dir_path, whitelist)):
                vm.log.info("Copying whitelisted apps list: {0}".
                    format(whitelist))
                shutil.copy(os.path.join(src_vm.dir_path, whitelist),
                            os.path.join(vm.dir_path, whitelist))

        # Create appmenus
        self.appicons_create(vm)
        self.appmenus_create(vm)


    @qubes.ext.handler('domain-remove-from-disk')
    def remove_from_disk(self, vm, event):
        self.appmenus_remove(vm)


    @qubes.ext.handler('property-set:label')
    def label_setter(self, vm, event, *args):
        if not os.path.exists(vm.dir_path):
            return
        self.appicons_create(vm, force=True)

        # Apparently desktop environments heavily caches the icons,
        # see #751 for details
        if "plasma" in os.environ.get("DESKTOP_SESSION", ""):
            try:
                os.unlink(os.path.expandvars(
                    "$HOME/.kde/cache-$HOSTNAME/icon-cache.kcache"))
            except:
                pass
            try:
                notify_object = dbus.SessionBus().get_object(
                    "org.freedesktop.Notifications",
                    "/org/freedesktop/Notifications")
                notify_object.Notify(
                    "Qubes", 0, vm.label.icon, "Qubes",
                    "You will need to log off and log in again for the VM icons "
                    "to update in the KDE launcher menu",
                    [], [], 10000,
                    dbus_interface="org.freedesktop.Notifications")
            except:
                pass
        elif "xfce" in os.environ.get("DESKTOP_SESSION", ""):
            self.appmenus_remove(vm)
            self.appmenus_create(vm)

    @qubes.ext.handler('property-set:internal')
    def on_property_set_internal(self, vm, event, prop, newvalue, *args):
        if not os.path.exists(vm.dir_path):
            return
        if len(args):
            oldvalue = args[0]
        else:
            oldvalue = vm.__class__.internal._default
        if newvalue and not oldvalue:
            self.appmenus_remove(vm)
        elif not newvalue and oldvalue:
            self.appmenus_create(vm)

    @qubes.ext.handler('backup-get-files')
    def files_for_backup(self, vm, event):
        if not os.path.exists(vm.dir_path):
            return
        if vm.internal:
            return
        if vm.updateable:
            yield self.templates_dir(vm)
            yield self.template_icons_dir(vm)
        if os.path.exists(self.whitelist_path(vm)):
            yield self.whitelist_path(vm)
        for whitelist in (
                'vm-' + AppmenusSubdirs.whitelist,
                'netvm-' + AppmenusSubdirs.whitelist):
            if os.path.exists(os.path.join(vm.dir_path, whitelist)):
                yield os.path.join(vm.dir_path, whitelist)
