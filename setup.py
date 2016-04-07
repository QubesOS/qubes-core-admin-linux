# vim: fileencoding=utf-8

import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='qubeslinux',
        version=open('version').read().strip(),
        author='Invisible Things Lab',
        author_email='woju@invisiblethingslab.com',
        description='Qubes core-linux package',
        license='GPL2+',
        url='https://www.qubes-os.org/',

        packages=('qubesappmenus',),

        entry_points={
            'console_scripts': [
                'qvm-sync-appmenus = qubesappmenus.receive:main'
            ],
            'qubes.ext': [
                'qubesappmenus = qubesappmenus:AppmenusExtension'
            ],
            'qubes.tests.extra': [
                'qubesappmenus = qubesappmenus.tests:list_tests',
            ],
        }
    )
