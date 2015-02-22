#!/bin/sh
VMNAME=$1
VMTYPE=$2
if [ -z "$VMTYPE" ]; then
    VMTYPE=appvms
fi
VMDIR=/var/lib/qubes/$VMTYPE/$VMNAME
APPSDIR=$VMDIR/apps

if [ $# -lt 1 ]; then
    echo "usage: $0 <vmname> [appvms|vm-templates|servicevms]"
    exit
fi

if ls $APPSDIR/*.directory $APPSDIR/*.desktop > /dev/null 2>&1; then
    LC_COLLATE=C xdg-desktop-menu uninstall $APPSDIR/*.directory $APPSDIR/*.desktop
    rm -f $APPSDIR/*.desktop $APPSDIR/*.directory
    rm -f $HOME/.config/menus/applications-merged/user-$VMNAME-vm.menu
fi

if [ -n "$KDE_SESSION_UID" -a -z "$SKIP_CACHE_REBUILD" ]; then
    kbuildsycoca4
fi
