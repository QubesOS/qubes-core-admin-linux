#
# This is the SPEC file for creating binary RPMs for the Dom0.
#
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2013  Marek Marczykowski  <marmarek@invisiblethingslab.com>
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

%{!?python_sitearch: %define python_sitearch %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib(1)")}

%{!?version: %define version %(cat version)}

%define _dracutmoddir	/usr/lib/dracut/modules.d
%if %{fedora} < 17
%define _dracutmoddir   /usr/share/dracut/modules.d
%endif

Name:		qubes-core-dom0-linux
Version:	%{version}
Release:	1%{?dist}
Summary:	Linux-specific files for Qubes dom0

Group:		Qubes
Vendor:		Invisible Things Lab
License:	GPL
URL:		http://www.qubes-os.org

BuildRequires:  ImageMagick
BuildRequires:  pandoc
BuildRequires:  qubes-utils-devel >= 3.1.3
BuildRequires:  qubes-libvchan-devel
Requires:	qubes-core-dom0
Requires:	qubes-utils >= 3.1.3
Requires:	%{name}-kernel-install
Requires:	xdotool

%define _builddir %(pwd)

%description
Linux customizations required to use system as Qubes dom0.
Additionally some graphical elements for every Linux desktop envirnment (icons,
appmenus etc).

%package kernel-install
Summary:	Kernel install hook for Xen-based system

# get rid of os-prober, it tries to mount and parse all the block devices in
# the system, including loop*
Provides: os-prober
Obsoletes: os-prober

%description kernel-install
Kernel install hook for Xen-based system.

%prep
# we operate on the current directory, so no need to unpack anything
# symlink is to generate useful debuginfo packages
rm -f %{name}-%{version}
ln -sf . %{name}-%{version}
%setup -T -D

%build
python -m compileall appmenus-scripts
python -O -m compileall appmenus-scripts
(cd dom0-updates; make)
(cd qrexec; make)
(cd file-copy-vm; make)
(cd doc; make manpages)

%install

### Appmenus

mkdir -p $RPM_BUILD_ROOT%{python_sitearch}/qubes/modules
cp appmenus-scripts/qubes-core-appmenus.py $RPM_BUILD_ROOT%{python_sitearch}/qubes/modules/10appmenus.py
cp appmenus-scripts/qubes-core-appmenus.pyc $RPM_BUILD_ROOT%{python_sitearch}/qubes/modules/10appmenus.pyc
cp appmenus-scripts/qubes-core-appmenus.pyo $RPM_BUILD_ROOT%{python_sitearch}/qubes/modules/10appmenus.pyo

mkdir -p $RPM_BUILD_ROOT/usr/libexec/qubes-appmenus
cp appmenus-scripts/*.sh $RPM_BUILD_ROOT/usr/libexec/qubes-appmenus/
cp appmenus-scripts/qubes-receive-appmenus $RPM_BUILD_ROOT/usr/libexec/qubes-appmenus/

install -D appmenus-scripts/qvm-sync-appmenus $RPM_BUILD_ROOT/usr/bin/qvm-sync-appmenus

mkdir -p $RPM_BUILD_ROOT/etc/qubes-rpc/policy
cp appmenus-scripts/qubes.SyncAppMenus $RPM_BUILD_ROOT/etc/qubes-rpc/
cp appmenus-scripts/qubes.SyncAppMenus.policy $RPM_BUILD_ROOT/etc/qubes-rpc/policy/qubes.SyncAppMenus

mkdir -p $RPM_BUILD_ROOT/usr/share/qubes-appmenus/
cp -r appmenus-files/* $RPM_BUILD_ROOT/usr/share/qubes-appmenus/

### Dom0 updates
install -D dom0-updates/qubes-dom0-updates.cron $RPM_BUILD_ROOT/etc/cron.daily/qubes-dom0-updates.cron
install -D dom0-updates/qubes-dom0-update $RPM_BUILD_ROOT/usr/bin/qubes-dom0-update
install -D dom0-updates/qubes-receive-updates $RPM_BUILD_ROOT/usr/libexec/qubes/qubes-receive-updates
install -D dom0-updates/patch-dnf-yum-config $RPM_BUILD_ROOT/usr/lib/qubes/patch-dnf-yum-config
install -m 0644 -D dom0-updates/qubes-cached.repo $RPM_BUILD_ROOT/etc/yum.real.repos.d/qubes-cached.repo
install -D dom0-updates/qfile-dom0-unpacker $RPM_BUILD_ROOT/usr/libexec/qubes/qfile-dom0-unpacker
install -m 0644 -D dom0-updates/qubes.ReceiveUpdates $RPM_BUILD_ROOT/etc/qubes-rpc/qubes.ReceiveUpdates
install -m 0664 -D dom0-updates/qubes.ReceiveUpdates.policy $RPM_BUILD_ROOT/etc/qubes-rpc/policy/qubes.ReceiveUpdates

install -d $RPM_BUILD_ROOT/var/lib/qubes/updates

# Qrexec
mkdir -p $RPM_BUILD_ROOT/usr/lib/qubes/
cp qrexec/qrexec-daemon $RPM_BUILD_ROOT/usr/lib/qubes/
cp qrexec/qrexec-client $RPM_BUILD_ROOT/usr/lib/qubes/
# XXX: Backward compatibility
ln -s qrexec-client $RPM_BUILD_ROOT/usr/lib/qubes/qrexec_client
cp qrexec/qrexec-policy $RPM_BUILD_ROOT/usr/lib/qubes/
cp qrexec/qubes-rpc-multiplexer $RPM_BUILD_ROOT/usr/lib/qubes

### pm-utils
mkdir -p $RPM_BUILD_ROOT/usr/lib64/pm-utils/sleep.d
cp pm-utils/01qubes-sync-vms-clock $RPM_BUILD_ROOT/usr/lib64/pm-utils/sleep.d/
cp pm-utils/51qubes-suspend-netvm $RPM_BUILD_ROOT/usr/lib64/pm-utils/sleep.d/
cp pm-utils/52qubes-pause-vms $RPM_BUILD_ROOT/usr/lib64/pm-utils/sleep.d/
mkdir -p $RPM_BUILD_ROOT/usr/lib/systemd/system
cp pm-utils/qubes-suspend.service $RPM_BUILD_ROOT/usr/lib/systemd/system/

### Dracut module
mkdir -p $RPM_BUILD_ROOT/etc/dracut.conf.d
cp dracut/dracut.conf.d/* $RPM_BUILD_ROOT/etc/dracut.conf.d/

mkdir -p $RPM_BUILD_ROOT%{_dracutmoddir}
cp -r dracut/modules.d/* $RPM_BUILD_ROOT%{_dracutmoddir}/

### Others
mkdir -p $RPM_BUILD_ROOT/etc/sysconfig
install -m 0644 -D system-config/limits-qubes.conf $RPM_BUILD_ROOT/etc/security/limits.d/99-qubes.conf
install -D system-config/cpufreq-xen.modules $RPM_BUILD_ROOT/etc/sysconfig/modules/cpufreq-xen.modules
install -m 0440 -D system-config/qubes.sudoers $RPM_BUILD_ROOT/etc/sudoers.d/qubes
install -D system-config/polkit-1-qubes-allow-all.rules $RPM_BUILD_ROOT/etc/polkit-1/rules.d/00-qubes-allow-all.rules
install -D system-config/qubes-dom0.modules $RPM_BUILD_ROOT/etc/sysconfig/modules/qubes-dom0.modules
install -D system-config/qubes-sync-clock.cron $RPM_BUILD_ROOT/etc/cron.d/qubes-sync-clock.cron
install -d $RPM_BUILD_ROOT/etc/udev/rules.d
install -m 644 system-config/00-qubes-ignore-devices.rules $RPM_BUILD_ROOT/etc/udev/rules.d/
install -m 644 system-config/60-persistent-storage.rules $RPM_BUILD_ROOT/etc/udev/rules.d/
install -m 644 -D system-config/disable-lesspipe $RPM_BUILD_ROOT/etc/profile.d/zz-disable-lesspipe
install -m 755 -D system-config/kernel-grub2.install $RPM_BUILD_ROOT/usr/lib/kernel/install.d/90-grub2.install
install -m 755 -D system-config/kernel-xen-efi.install $RPM_BUILD_ROOT/usr/lib/kernel/install.d/90-xen-efi.install
install -m 755 -D system-config/kernel-remove-bls.install $RPM_BUILD_ROOT/usr/lib/kernel/install.d/99-remove-bls.install
install -m 644 -D system-config/75-qubes-dom0.preset \
    $RPM_BUILD_ROOT/usr/lib/systemd/system-preset/75-qubes-dom0.preset
install -m 644 -D system-config/99-qubes-default-disable.preset \
    $RPM_BUILD_ROOT/usr/lib/systemd/system-preset/99-qubes-default-disable.preset
install -m 755 qvm-xkill $RPM_BUILD_ROOT/usr/bin/

# file copy to VM
install -m 755 file-copy-vm/qfile-dom0-agent $RPM_BUILD_ROOT/usr/lib/qubes/
install -m 755 file-copy-vm/qvm-copy-to-vm $RPM_BUILD_ROOT/usr/bin/
install -m 755 file-copy-vm/qvm-move-to-vm $RPM_BUILD_ROOT/usr/bin/

### Icons
mkdir -p $RPM_BUILD_ROOT/usr/share/qubes/icons
for icon in icons/*.png; do
    convert -resize 48 $icon $RPM_BUILD_ROOT/usr/share/qubes/$icon
done

### Documentation
(cd doc; make DESTDIR=$RPM_BUILD_ROOT install)

%pre
if ! grep -q ^qubes: /etc/group ; then
		groupadd qubes
fi

%post

for i in /usr/share/qubes/icons/*.png ; do
	xdg-icon-resource install --noupdate --novendor --size 48 $i
done
xdg-icon-resource forceupdate

xdg-desktop-menu install /usr/share/qubes-appmenus/qubes-dispvm.directory /usr/share/qubes-appmenus/qubes-dispvm-*.desktop

/usr/lib/qubes/patch-dnf-yum-config

systemctl enable qubes-suspend.service >/dev/null 2>&1

%preun
if [ "$1" = 0 ] ; then
	# no more packages left

	for i in /usr/share/qubes/icons/*.png ; do
		xdg-icon-resource uninstall --novendor --size 48 $i
	done

    xdg-desktop-menu uninstall /usr/share/qubes-appmenus/qubes-dispvm.directory /usr/share/qubes-appmenus/qubes-dispvm-*.desktop

    systemctl disable qubes-suspend.service > /dev/null 2>&1
fi

%triggerin -- PackageKit
# dom0 have no network, but still can receive updates (qubes-dom0-update)
sed -i 's/^UseNetworkHeuristic=.*/UseNetworkHeuristic=false/' /etc/PackageKit/PackageKit.conf

%triggerin -- xorg-x11-drv-vmmouse
# Remove unnecessary udev rules that causes problems in dom0 (#605)
rm -f /lib/udev/rules.d/69-xorg-vmmouse.rules

%triggerin -- grub2-tools
chmod -x /etc/grub.d/10_linux

%files
%attr(2775,root,qubes) %dir /etc/qubes-rpc
%attr(2775,root,qubes) %dir /etc/qubes-rpc/policy
/etc/qubes-rpc/policy/qubes.SyncAppMenus
/etc/qubes-rpc/qubes.SyncAppMenus
%{python_sitearch}/qubes/modules/10appmenus.py
%{python_sitearch}/qubes/modules/10appmenus.pyc
%{python_sitearch}/qubes/modules/10appmenus.pyo
/usr/libexec/qubes-appmenus/convert-apptemplate2vm.sh
/usr/libexec/qubes-appmenus/convert-dirtemplate2vm.sh
/usr/libexec/qubes-appmenus/create-apps-for-appvm.sh
/usr/libexec/qubes-appmenus/qubes-receive-appmenus
/usr/libexec/qubes-appmenus/remove-appvm-appmenus.sh
/usr/share/qubes-appmenus/qubes-appmenu-select.desktop
/usr/share/qubes-appmenus/qubes-dispvm-firefox.desktop
/usr/share/qubes-appmenus/qubes-dispvm-xterm.desktop
/usr/share/qubes-appmenus/qubes-dispvm.directory
/usr/share/qubes-appmenus/qubes-servicevm.directory.template
/usr/share/qubes-appmenus/qubes-start.desktop
/usr/share/qubes-appmenus/qubes-templatevm.directory.template
/usr/share/qubes-appmenus/qubes-vm.directory.template
/usr/share/qubes-appmenus/hvm
/usr/share/qubes/icons/*.png
/usr/bin/qvm-sync-appmenus
# Dom0 updates
/etc/cron.daily/qubes-dom0-updates.cron
/etc/yum.real.repos.d/qubes-cached.repo
/usr/bin/qubes-dom0-update
/usr/lib/qubes/patch-dnf-yum-config
%attr(4750,root,qubes) /usr/libexec/qubes/qfile-dom0-unpacker
/usr/libexec/qubes/qubes-receive-updates
/etc/qubes-rpc/qubes.ReceiveUpdates
%attr(0664,root,qubes) %config(noreplace) /etc/qubes-rpc/policy/qubes.ReceiveUpdates
%attr(0770,root,qubes) %dir /var/lib/qubes/updates
# Dracut module
/etc/dracut.conf.d/*
%dir %{_dracutmoddir}/90qubes-pciback
%{_dracutmoddir}/90qubes-pciback/*
%dir %{_dracutmoddir}/90extra-modules
%{_dracutmoddir}/90extra-modules/*
# Qrexec
%attr(4750,root,qubes) /usr/lib/qubes/qrexec-daemon
/usr/lib/qubes/qrexec-client
/usr/lib/qubes/qrexec_client
/usr/lib/qubes/qubes-rpc-multiplexer
/usr/lib/qubes/qrexec-policy
# file copy
/usr/bin/qvm-copy-to-vm
/usr/bin/qvm-move-to-vm
/usr/lib/qubes/qfile-dom0-agent
# pm-utils
/usr/lib64/pm-utils/sleep.d/01qubes-sync-vms-clock
/usr/lib64/pm-utils/sleep.d/51qubes-suspend-netvm
/usr/lib64/pm-utils/sleep.d/52qubes-pause-vms
/usr/lib/systemd/system/qubes-suspend.service
# Others
/etc/sysconfig/modules/qubes-dom0.modules
/etc/sysconfig/modules/cpufreq-xen.modules
/etc/sudoers.d/qubes
/etc/polkit-1/rules.d/00-qubes-allow-all.rules
/etc/security/limits.d/99-qubes.conf
%config /etc/udev/rules.d/00-qubes-ignore-devices.rules
%config(noreplace) /etc/udev/rules.d/60-persistent-storage.rules
%attr(0644,root,root) /etc/cron.d/qubes-sync-clock.cron
%config(noreplace) /etc/profile.d/zz-disable-lesspipe
/usr/lib/systemd/system-preset/75-qubes-dom0.preset
/usr/lib/systemd/system-preset/99-qubes-default-disable.preset
/usr/bin/qvm-xkill
# Man
%{_mandir}/man1/qvm-*.1*
%{_mandir}/man1/qubes-*.1*

%files kernel-install
/usr/lib/kernel/install.d/90-grub2.install
/usr/lib/kernel/install.d/90-xen-efi.install
/usr/lib/kernel/install.d/99-remove-bls.install

%changelog
