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

import qubes.ext

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
    appmenu_create_cmd = \
        '/usr/libexec/qubes-appmenus/create-apps-for-appvm.sh'
    appmenu_remove_cmd = \
        '/usr/libexec/qubes-appmenus/remove-appvm-appmenus.sh'


class AppmenusExtension(qubes.ext.Extension):
    def __init__(self, *args):
        super(AppmenusExtension, self).__init__(*args)
        import qubes.vm.qubesvm

    def templates_dir(self, vm):
        """

        :type vm: qubes.vm.qubesvm.QubesVM
        """
        assert isinstance(vm, qubes.vm.qubesvm.QubesVM)
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

    def appmenus_create(self, vm, source_template=None):
        if source_template is None and hasattr(vm, 'template'):
            source_template = vm.template

        if vm.internal:
            return
        if vm.is_disposablevm():
            return

        vmsubdir = vm.dir_path.split(os.path.sep)[-2]

        try:
            #TODO msgoutput = None if verbose else open(os.devnull, 'w')
            msgoutput = None
            if source_template is not None:
                subprocess.check_call([AppmenusPaths.appmenu_create_cmd,
                                       self.templates_dir(source_template),
                                       vm.name, vmsubdir, vm.label.icon],
                                      stdout=msgoutput, stderr=msgoutput)
            elif self.templates_dir(vm) is not None:
                subprocess.check_call([AppmenusPaths.appmenu_create_cmd,
                                       self.templates_dir(vm), vm.name,
                                       vmsubdir, vm.label.icon],
                                      stdout=msgoutput, stderr=msgoutput)
            else:
                # Only add apps to menu
                subprocess.check_call([AppmenusPaths.appmenu_create_cmd,
                                       "none", vm.name, vmsubdir,
                                       vm.label.icon],
                                      stdout=msgoutput, stderr=msgoutput)
        except subprocess.CalledProcessError:
            vm.log.warning("Ooops, there was a problem creating appmenus "
                                 "for {0} VM!")


    def appmenus_remove(self, vm):
        vmsubdir = vm.dir_path.split(os.path.sep)[-2]
        subprocess.check_call([AppmenusPaths.appmenu_remove_cmd, vm.name,
                               vmsubdir], stderr=open(os.devnull, 'w'))

    def appmenus_cleanup(self, vm):
        srcdir = self.templates_dir(vm)
        if srcdir is None:
            return
        if not os.path.exists(srcdir):
            return
        if not os.path.exists(self.appmenus_dir(vm)):
            return

        for appmenu in os.listdir(self.appmenus_dir(vm)):
            if not os.path.exists(os.path.join(srcdir, appmenu)):
                os.unlink(os.path.join(self.appmenus_dir(vm), appmenu))

    def appicons_create(self, vm, srcdir=None, force=False):
        if srcdir is None:
            srcdir = self.template_icons_dir(vm)
        if srcdir is None:
            return
        if not os.path.exists(srcdir):
            return

        if vm.internal:
            return
        if vm.is_disposablevm():
            return

        whitelist = os.path.join(vm.dir_path, AppmenusSubdirs.whitelist)
        if os.path.exists(whitelist):
            whitelist = [line.strip() for line in open(whitelist)]
        else:
            whitelist = None

        if not os.path.exists(self.icons_dir(vm)):
            os.mkdir(self.icons_dir(vm))
        elif not os.path.isdir(self.icons_dir(vm)):
            os.unlink(self.icons_dir(vm))
            os.mkdir(self.icons_dir(vm))

        for icon in os.listdir(srcdir):
            desktop = os.path.splitext(icon)[0] + '.desktop'
            if whitelist and desktop not in whitelist:
                continue

            src_icon = os.path.join(srcdir, icon)
            dst_icon = os.path.join(self.icons_dir(vm), icon)
            if not os.path.exists(dst_icon) or force or \
                    os.path.getmtime(src_icon) > os.path.getmtime(dst_icon):
                qubesimgconverter.tint(src_icon, dst_icon, vm.label.color)

    def appicons_remove(self, vm):
        if not os.path.exists(self.icons_dir(vm)):
            return
        for icon in os.listdir(self.icons_dir(vm)):
            os.unlink(os.path.join(self.icons_dir(vm), icon))


    def appicons_cleanup(self, vm):
        srcdir = self.template_icons_dir(vm)
        if srcdir is None:
            return
        if not os.path.exists(srcdir):
            return
        if not os.path.exists(self.icons_dir(vm)):
            return

        for icon in os.listdir(self.icons_dir(vm)):
            if not os.path.exists(os.path.join(srcdir, icon)):
                os.unlink(os.path.join(self.icons_dir(vm), icon))

    @qubes.ext.handler('property-pre-set:name')
    def pre_rename(self, vm, event, prop, *args):
        self.appmenus_remove(vm)

    @qubes.ext.handler('property-set:name')
    def post_rename(self, vm, event, prop, *args):
        self.appmenus_create(vm)

    @qubes.ext.handler('domain-create-on-disk')
    def create_on_disk(self, vm, event, source_template=None):
        if vm.hvm and source_template is None:
            vm.log.info("Creating appmenus directory: {0}".format(
                self.templates_dir(vm)))
            os.mkdir(self.templates_dir(vm))
            shutil.copy(AppmenusPaths.appmenu_start_hvm_template,
                        self.templates_dir(vm))

        source_whitelist_filename = 'vm-' + AppmenusSubdirs.whitelist
        if vm.is_netvm():
            source_whitelist_filename = 'netvm-' + AppmenusSubdirs.whitelist
        if source_template and os.path.exists(
                os.path.join(source_template.dir_path, source_whitelist_filename)):
            vm.log.info("Creating default whitelisted apps list: {0}".
                    format(vm.dir_path + '/' + AppmenusSubdirs.whitelist))
            shutil.copy(
                os.path.join(source_template.dir_path, source_whitelist_filename),
                os.path.join(vm.dir_path, AppmenusSubdirs.whitelist))

        if source_template and vm.updateable:
            vm.log.info("--> Copying the template's appmenus templates "
                        "dir:\n{0} ==>\n{1}".
                    format(self.templates_dir(source_template),
                           self.templates_dir(vm)))
            if os.path.isdir(self.templates_dir(source_template)):
                shutil.copytree(self.templates_dir(source_template),
                                self.templates_dir(vm))
            else:
                os.mkdir(self.templates_dir(vm))
            if os.path.isdir(self.template_icons_dir(source_template)):
                shutil.copytree(self.template_icons_dir(source_template),
                                self.template_icons_dir(vm))
            else:
                os.mkdir(self.template_icons_dir(vm))

        # Create appmenus
        self.appicons_create(vm)
        self.appmenus_create(vm)

    @qubes.ext.handler('domain-clone-files')
    def clone_disk_files(self, vm, event, src_vm):
        if src_vm.updateable and src_vm.templates_dir(vm) is not None and \
                self.templates_dir(vm) is not None:
            vm.log.info("Copying the template's appmenus templates "
                        "dir:\n{0} ==>\n{1}".
                    format(self.templates_dir(src_vm),
                           self.templates_dir(vm)))
            shutil.copytree(self.templates_dir(src_vm),
                            self.templates_dir(vm))

        if src_vm.updateable and src_vm.template_icons_dir(vm) is not None \
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

    def appmenus_recreate(self, vm):
        """
        Force recreation of all appmenus and icons. For example when VM label
        color was changed
        """
        self.appmenus_remove(vm)
        self.appmenus_cleanup(vm)
        self.appicons_remove(vm)
        self.appicons_create(vm)
        self.appmenus_create(vm)

    def appmenus_update(self, vm):
        """
        Similar to appmenus_recreate, but do not touch unchanged files
        """
        self.appmenus_remove(vm)
        self.appmenus_cleanup(vm)
        self.appicons_create(vm)
        self.appicons_cleanup(vm)
        self.appmenus_create(vm)

    @qubes.ext.handler('property-set:internal')
    def set_attr(self, vm, event, prop, newvalue, *args):
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
        if vm.internal:
            return
        if vm.updateable:
            yield self.templates_dir(vm)
            yield self.template_icons_dir(vm)
        yield os.path.join(vm.dir_path, AppmenusSubdirs.whitelist)
        if vm.is_template():
            for whitelist in (
                    'vm-' + AppmenusSubdirs.whitelist,
                    'netvm-' + AppmenusSubdirs.whitelist):
                yield os.path.join(vm.dir_path, whitelist)
