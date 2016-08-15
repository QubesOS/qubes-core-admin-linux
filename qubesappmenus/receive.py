#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2011  Marek Marczykowski <marmarek@mimuw.edu.pl>
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#
import optparse

import subprocess
import re
import os
import sys
import shutil
import pipes

from optparse import OptionParser
import qubes.exc
import qubes.tools
import qubesappmenus

import qubesimgconverter

parser = qubes.tools.QubesArgumentParser(
    vmname_nargs='?',
    want_force_root=True,
    description='retrieve appmenus')

parser.add_argument('--force-rpc',
    action='store_true', default=False,
    help="Force to start a new RPC call, even if called from existing one")

# TODO offline mode

# fields required to be present (and verified) in retrieved desktop file
required_fields = ["Name", "Exec"]

# limits
appmenus_line_size = 1024
appmenus_line_count = 100000

# regexps for sanitization of retrieved values
std_re = re.compile(r"^[/a-zA-Z0-9.,:&()_ -]*$")
fields_regexp = {
    "Name": std_re,
    "GenericName": std_re,
    "Comment": std_re,
    "Categories": re.compile(r"^[a-zA-Z0-9/.;:'() -]*$"),
    "Exec": re.compile(r"^[a-zA-Z0-9()_%&>/{}\"'\\:.= -]*$"),
    "Icon": re.compile(r"^[a-zA-Z0-9/_.-]*$"),
}

CATEGORIES_WHITELIST = {
    # Main Categories
    # http://standards.freedesktop.org/menu-spec/1.1/apa.html 20140507
    'AudioVideo', 'Audio', 'Video', 'Development', 'Education', 'Game',
    'Graphics', 'Network', 'Office', 'Science', 'Settings', 'System',
    'Utility',

    # Additional Categories
    # http://standards.freedesktop.org/menu-spec/1.1/apas02.html
    'Building', 'Debugger', 'IDE', 'GUIDesigner', 'Profiling',
    'RevisionControl', 'Translation', 'Calendar', 'ContactManagement',
    'Database', 'Dictionary', 'Chart', 'Email', 'Finance', 'FlowChart', 'PDA',
    'ProjectManagement', 'Presentation', 'Spreadsheet', 'WordProcessor',
    '2DGraphics', 'VectorGraphics', 'RasterGraphics', '3DGraphics', 'Scanning',
    'OCR', 'Photography', 'Publishing', 'Viewer', 'TextTools',
    'DesktopSettings', 'HardwareSettings', 'Printing', 'PackageManager',
    'Dialup', 'InstantMessaging', 'Chat', 'IRCClient', 'Feed', 'FileTransfer',
    'HamRadio', 'News', 'P2P', 'RemoteAccess', 'Telephony', 'TelephonyTools',
    'VideoConference', 'WebBrowser', 'WebDevelopment', 'Midi', 'Mixer',
    'Sequencer', 'Tuner', 'TV', 'AudioVideoEditing', 'Player', 'Recorder',
    'DiscBurning', 'ActionGame', 'AdventureGame', 'ArcadeGame', 'BoardGame',
    'BlocksGame', 'CardGame', 'KidsGame', 'LogicGame', 'RolePlaying',
    'Shooter', 'Simulation', 'SportsGame', 'StrategyGame', 'Art',
    'Construction', 'Music', 'Languages', 'ArtificialIntelligence',
    'Astronomy', 'Biology', 'Chemistry', 'ComputerScience',
    'DataVisualization', 'Economy', 'Electricity', 'Geography', 'Geology',
    'Geoscience', 'History', 'Humanities', 'ImageProcessing', 'Literature',
    'Maps', 'Math', 'NumericalAnalysis', 'MedicalSoftware', 'Physics',
    'Robotics', 'Spirituality', 'Sports', 'ParallelComputing', 'Amusement',
    'Archiving', 'Compression', 'Electronics', 'Emulator', 'Engineering',
    'FileTools', 'FileManager', 'TerminalEmulator', 'Filesystem', 'Monitor',
    'Security', 'Accessibility', 'Calculator', 'Clock', 'TextEditor',
    'Documentation', 'Adult', 'Core', 'KDE', 'GNOME', 'XFCE', 'GTK', 'Qt',
    'Motif', 'Java', 'ConsoleOnly',

    # Reserved Categories (not whitelisted)
    # http://standards.freedesktop.org/menu-spec/1.1/apas03.html
    # 'Screensaver', 'TrayIcon', 'Applet', 'Shell',
}


def sanitise_categories(untrusted_value):
    untrusted_categories = (c.strip() for c in untrusted_value.split(';') if c)
    categories = (c for c in untrusted_categories if c in CATEGORIES_WHITELIST)

    return ';'.join(categories) + ';'


def fallback_hvm_appmenulist():
    p = subprocess.Popen(["grep", "-rH", "=", "/usr/share/qubes-appmenus/hvm"],
                         stdout=subprocess.PIPE)
    (stdout, stderr) = p.communicate()
    return stdout.splitlines()


def get_appmenus(vm):
    appmenus_line_limit_left = appmenus_line_count
    untrusted_appmenulist = []
    if vm is None:
        while appmenus_line_limit_left > 0:
            untrusted_line = sys.stdin.readline(appmenus_line_size)
            if untrusted_line == "":
                break
            untrusted_appmenulist.append(untrusted_line.strip())
            appmenus_line_limit_left -= 1
        if appmenus_line_limit_left == 0:
            raise qubes.exc.QubesException("Line count limit exceeded")
    else:
        p = vm.run('QUBESRPC qubes.GetAppmenus dom0', passio_popen=True,
                   gui=False)
        while appmenus_line_limit_left > 0:
            untrusted_line = p.stdout.readline(appmenus_line_size)
            if untrusted_line == "":
                break
            untrusted_appmenulist.append(untrusted_line.strip())
            appmenus_line_limit_left -= 1
        p.wait()
        if p.returncode != 0:
            if vm.hvm:
                untrusted_appmenulist = fallback_hvm_appmenulist()
            else:
                raise qubes.exc.QubesException("Error getting application list")
        if appmenus_line_limit_left == 0:
            raise qubes.exc.QubesException("Line count limit exceeded")

    appmenus = {}
    line_rx = re.compile(
        r"([a-zA-Z0-9.()_-]+.desktop):([a-zA-Z0-9-]+(?:\[[a-zA-Z@_]+\])?)=(.*)")
    ignore_rx = re.compile(r".*([a-zA-Z0-9._-]+.desktop):(#.*|\s+)$")
    for untrusted_line in untrusted_appmenulist:
        # Ignore blank lines and comments
        if len(untrusted_line) == 0 or ignore_rx.match(untrusted_line):
            continue
        # use search instead of match to skip file path
        untrusted_m = line_rx.search(untrusted_line)
        if untrusted_m:
            filename = untrusted_m.group(1)
            assert '/' not in filename
            assert '\0' not in filename

            untrusted_key = untrusted_m.group(2)
            assert '\0' not in untrusted_key
            assert '\x1b' not in untrusted_key
            assert '=' not in untrusted_key

            untrusted_value = untrusted_m.group(3)
            # TODO add key-dependent asserts

            # Look only at predefined keys
            if untrusted_key in fields_regexp:
                if fields_regexp[untrusted_key].match(untrusted_value):
                    # now values are sanitized
                    key = untrusted_key
                    if key == 'Categories':
                        value = sanitise_categories(untrusted_value)
                    else:
                        value = untrusted_value

                    if filename not in appmenus:
                        appmenus[filename] = {}

                    appmenus[filename][key] = value
                else:
                    print >> sys.stderr, \
                        "Warning: ignoring key %r of %s" % \
                        (untrusted_key, filename)
            # else: ignore this key

    return appmenus


def create_template(path, values):
    # check if all required fields are present
    for key in required_fields:
        if key not in values:
            print >> sys.stderr, "Warning: not creating/updating '%s' " \
                                 "because of missing '%s' key" % (
                                     path, key)
            return

    desktop_entry = ""
    desktop_entry += "[Desktop Entry]\n"
    desktop_entry += "Version=1.0\n"
    desktop_entry += "Type=Application\n"
    desktop_entry += "Terminal=false\n"
    desktop_entry += "X-Qubes-VmName=%VMNAME%\n"

    if 'Icon' in values:
        icon_file = os.path.splitext(os.path.split(path)[1])[0] + '.png'
        desktop_entry += "Icon={0}\n".format(os.path.join(
            '%VMDIR%', qubesappmenus.AppmenusSubdirs.icons_subdir, icon_file))
    else:
        desktop_entry += "Icon=%XDGICON%\n"

    for key in ["Name", "GenericName"]:
        if key in values:
            desktop_entry += "{0}=%VMNAME%: {1}\n".format(key, values[key])

    # force category X-Qubes-VM
    values["Categories"] = values.get("Categories", "") + "X-Qubes-VM;"

    for key in ["Comment", "Categories"]:
        if key in values:
            desktop_entry += "{0}={1}\n".format(key, values[key])

    desktop_entry += "Exec=qvm-run -q --tray -a %VMNAME% -- {0}\n".format(
        pipes.quote(values['Exec']))
    if not os.path.exists(path) or desktop_entry != open(path, "r").read():
        desktop_file = open(path, "w")
        desktop_file.write(desktop_entry)
        desktop_file.close()

def process_appmenus_templates(appmenusext, vm, appmenus):
    old_umask = os.umask(002)

    if not os.path.exists(appmenusext.templates_dir(vm)):
        os.mkdir(appmenusext.templates_dir(vm))

    if not os.path.exists(appmenusext.template_icons_dir(vm)):
        os.mkdir(appmenusext.template_icons_dir(vm))

    if vm.hvm:
        if not os.path.exists(os.path.join(
                appmenusext.templates_dir(vm),
                os.path.basename(
                    qubesappmenus.AppmenusPaths.appmenu_start_hvm_template))):
            shutil.copy(qubesappmenus.AppmenusPaths.appmenu_start_hvm_template,
                appmenusext.templates_dir(vm))


    for appmenu_file in appmenus.keys():
        if os.path.exists(
                os.path.join(appmenusext.templates_dir(vm),
                    appmenu_file)):
            vm.log.info("Updating {0}".format(appmenu_file))
        else:
            vm.log.info("Creating {0}".format(appmenu_file))

        # TODO: icons support in offline mode
        # TODO if options.offline_mode:
        # TODO     new_appmenus[appmenu_file].pop('Icon', None)
        if 'Icon' in appmenus[appmenu_file]:
            # the following line is used for time comparison
            icondest = os.path.join(appmenusext.template_icons_dir(vm),
                                    os.path.splitext(appmenu_file)[0] + '.png')

            try:
                icon = qubesimgconverter.Image. \
                    get_xdg_icon_from_vm(vm, appmenus[appmenu_file]['Icon'])
                if os.path.exists(icondest):
                    old_icon = qubesimgconverter.Image.load_from_file(icondest)
                else:
                    old_icon = None
                if old_icon is None or icon != old_icon:
                    icon.save(icondest)
            except Exception, e:
                vm.log.warning('Failed to get icon for {0}: {1!s}'.\
                    format(appmenu_file, e))

                if os.path.exists(icondest):
                    vm.log.warning('Found old icon, using it instead')
                else:
                    del appmenus[appmenu_file]['Icon']

        create_template(os.path.join(appmenusext.templates_dir(vm),
            appmenu_file), appmenus[appmenu_file])

    # Delete appmenus of removed applications
    for appmenu_file in os.listdir(appmenusext.templates_dir(vm)):
        if not appmenu_file.endswith('.desktop'):
            continue

        if appmenu_file not in appmenus:
            vm.log.info("Removing {0}".format(appmenu_file))
            os.unlink(os.path.join(appmenusext.templates_dir(vm),
                appmenu_file))

    os.umask(old_umask)


def main(args=None):
    env_vmname = os.environ.get("QREXEC_REMOTE_DOMAIN")

    args = parser.parse_args(args)

    if env_vmname:
        vm = args.app.domains[env_vmname]
    else:
        vm = args.vm

    if args.vm is None:
        parser.error("You must specify at least the VM name!")

    if hasattr(vm, 'template'):
        raise qubes.exc.QubesException(
            "To sync appmenus for template based VM, do it on template instead")

    #TODO if not options.offline_mode and not vm.is_running():
    if not vm.is_running():
        raise qubes.exc.QubesVMNotRunningError(vm,
            "Appmenus can be retrieved only from running VM")

    # TODO if not options.offline_mode and env_vmname is None or
    # options.force_rpc:
    if env_vmname is None or args.force_rpc:
        new_appmenus = get_appmenus(vm)
    else:
        new_appmenus = get_appmenus(None)

    if len(new_appmenus) == 0:
        raise qubes.exc.QubesException("No appmenus received, terminating")

    appmenusext = qubesappmenus.AppmenusExtension()

    process_appmenus_templates(appmenusext, vm, new_appmenus)

    appmenusext.appmenus_update(vm)
    if hasattr(vm, 'appvms'):
        for child_vm in vm.appvms:
            try:
                appmenusext.appmenus_create(child_vm, refresh_cache=False)
            except Exception, e:
                child_vm.log.error("Failed to recreate appmenus for "
                    "'{0}': {1}".format(child_vm.name, str(e)))
        subprocess.call(['xdg-desktop-menu', 'forceupdate'])
        if 'KDE_SESSION_UID' in os.environ:
            subprocess.call(['kbuildsycoca' + os.environ.get('KDE_SESSION_VERSION', '4')])
