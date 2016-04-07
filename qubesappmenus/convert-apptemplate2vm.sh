#!/bin/sh
SRC=$1
DSTDIR=$2
VMNAME=$3
VMDIR=$4
XDGICON=$5

DST=$DSTDIR/$VMNAME-$(basename $SRC)

if ! [ -r "$SRC" ]; then
    exit 0
fi

sed \
    -e "s/%VMNAME%/$VMNAME/" \
    -e "s %VMDIR% $VMDIR " \
    -e "s/%XDGICON%/$XDGICON/" \
        <$SRC >$DST


