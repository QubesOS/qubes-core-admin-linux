#!/usr/bin/python3
#
# Copyright (C) 2022 Piotr Bartman <prbartman@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with this program; if not, see <http://www.gnu.org/licenses/>.
import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='qubes-vmupdate',
        version=open('version').read().strip(),
        author='Invisible Things Lab',
        author_email='qubes-devel@googlegroups.com',
        description='Qubes VM updater',
        license='GPL2+',
        url='https://www.qubes-os.org/',
        packages=setuptools.find_packages(include=("vmupdate", "vmupdate*")),
        entry_points={
            'console_scripts':
                'qubes-vm-update = vmupdate.vmupdate:main',
        },
    )