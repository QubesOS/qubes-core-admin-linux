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
BuildRequires:  qubes-utils-devel
Requires:	qubes-core-dom0
Requires:	qubes-utils

%define _builddir %(pwd)

%description
Linux customizations required to use system as Qubes dom0.
Additionally some graphical elements for every Linux desktop envirnment (icons,
appmenus etc).

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
cp appmenus-files/* $RPM_BUILD_ROOT/usr/share/qubes-appmenus/

### Dom0 updates
install -D dom0-updates/qubes-dom0-updates.cron $RPM_BUILD_ROOT/etc/cron.daily/qubes-dom0-updates.cron
install -D dom0-updates/qubes-dom0-update $RPM_BUILD_ROOT/usr/bin/qubes-dom0-update
install -D dom0-updates/qubes-receive-updates $RPM_BUILD_ROOT/usr/libexec/qubes/qubes-receive-updates
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

### Dracut module
mkdir -p $RPM_BUILD_ROOT/etc/dracut.conf.d
cp dracut/dracut.conf.d/* $RPM_BUILD_ROOT/etc/dracut.conf.d/

mkdir -p $RPM_BUILD_ROOT%{_dracutmoddir}
cp -r dracut/modules.d/* $RPM_BUILD_ROOT%{_dracutmoddir}/

### Others
mkdir -p $RPM_BUILD_ROOT/etc/sysconfig
install -m 0644 -D system-config/limits-qubes.conf $RPM_BUILD_ROOT/etc/security/limits.d/99-qubes.conf
install -D system-config/cpufreq-xen.modules $RPM_BUILD_ROOT/etc/sysconfig/modules/cpufreq-xen.modules
cp system-config/iptables $RPM_BUILD_ROOT/etc/sysconfig
cp system-config/ip6tables $RPM_BUILD_ROOT/etc/sysconfig
install -m 0440 -D system-config/qubes.sudoers $RPM_BUILD_ROOT/etc/sudoers.d/qubes
install -D system-config/polkit-1-qubes-allow-all.rules $RPM_BUILD_ROOT/etc/polkit-1/rules.d/00-qubes-allow-all.rules
install -D system-config/qubes-dom0.modules $RPM_BUILD_ROOT/etc/sysconfig/modules/qubes-dom0.modules
install -D system-config/qubes-sync-clock.cron $RPM_BUILD_ROOT/etc/cron.d/qubes-sync-clock.cron

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
	xdg-icon-resource install --novendor --size 48 $i
done

xdg-desktop-menu install /usr/share/qubes-appmenus/qubes-dispvm.directory /usr/share/qubes-appmenus/qubes-dispvm-firefox.desktop

sed '/^reposdir\s*=/d' -i /etc/yum.conf
echo reposdir=/etc/yum.real.repos.d >> /etc/yum.conf

sed '/^installonlypkgs\s*=/d' -i /etc/yum.conf
echo 'installonlypkgs = kernel, kernel-qubes-vm' >> /etc/yum.conf

# Remove unnecessary udev rules that causes problems in dom0 (#605)
mkdir -p /var/lib/qubes/removed-udev-scripts
mv -f /lib/udev/rules.d/69-xorg-vmmouse.rules /var/lib/qubes/removed-udev-scripts/ 2> /dev/null || :

%preun
if [ "$1" = 0 ] ; then
	# no more packages left

	for i in /usr/share/qubes/icons/*.png ; do
		xdg-icon-resource uninstall --novendor --size 48 $i
	done

    xdg-desktop-menu uninstall /usr/share/qubes-appmenus/qubes-dispvm.directory /usr/share/qubes-appmenus/qubes-dispvm-firefox.desktop
fi

%triggerin -- PackageKit
# dom0 have no network, but still can receive updates (qubes-dom0-update)
sed -i 's/^UseNetworkHeuristic=.*/UseNetworkHeuristic=false/' /etc/PackageKit/PackageKit.conf

%triggerin -- xorg-x11-drv-vmmouse
mv -f /lib/udev/rules.d/69-xorg-vmmouse.rules /var/lib/qubes/removed-udev-scripts/ 2> /dev/null || :

%files
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
/usr/share/qubes-appmenus/qubes-dispvm.directory
/usr/share/qubes-appmenus/qubes-servicevm.directory.template
/usr/share/qubes-appmenus/qubes-start.desktop
/usr/share/qubes-appmenus/qubes-templatevm.directory.template
/usr/share/qubes-appmenus/qubes-vm.directory.template
/usr/share/qubes/icons/*.png
/usr/bin/qvm-sync-appmenus
# Dom0 updates
/etc/cron.daily/qubes-dom0-updates.cron
/etc/yum.real.repos.d/qubes-cached.repo
/usr/bin/qubes-dom0-update
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
# pm-utils
/usr/lib64/pm-utils/sleep.d/01qubes-sync-vms-clock
/usr/lib64/pm-utils/sleep.d/51qubes-suspend-netvm
/usr/lib64/pm-utils/sleep.d/52qubes-pause-vms
# Others
/etc/sysconfig/iptables
/etc/sysconfig/ip6tables
/etc/sysconfig/modules/qubes-dom0.modules
/etc/sysconfig/modules/cpufreq-xen.modules
/etc/sudoers.d/qubes
/etc/polkit-1/rules.d/00-qubes-allow-all.rules
/etc/security/limits.d/99-qubes.conf
%attr(0644,root,root) /etc/cron.d/qubes-sync-clock.cron
# Man
%{_mandir}/man1/qvm-*.1*
%{_mandir}/man1/qubes-*.1*


%changelog
