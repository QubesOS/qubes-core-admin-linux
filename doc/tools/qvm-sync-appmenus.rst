=================
qvm-sync-appmenus
=================

NAME
====
qvm-sync-appmenus - updates desktop file templates for given StandaloneVM or TemplateVM

SYNOPSIS
========
| qvm-sync-appmenus [options] <vm-name>

OPTIONS
=======
-h, --help
    Show this help message and exit
-v, --verbose
    Run in verbose mode 
-q, --quiet
    Run in quiet mode 
--regenerate-only
    Only regenerate appmenu entries, do not synchronize with system in template
--force-root
    Force running even if called as root
--force-rpc
    Force to start RPC call, even if called from one
 
AUTHORS
=======
| Joanna Rutkowska <joanna at invisiblethingslab dot com>
| Rafal Wojtczuk <rafal at invisiblethingslab dot com>
| Marek Marczykowski <marmarek at invisiblethingslab dot com>
