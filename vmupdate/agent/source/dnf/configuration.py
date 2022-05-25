from .manage_rpm_macro import manage_rpm_macro
from .disable_deltarpm import disable_deltarpm


def get_configured_dnf(os_data, requirements, loglevel):
    try:
        from .dnf_api import DNF
    except ImportError:
        # no progress reporting
        from .dnf_cli import DNFCLI as DNF

    manage_rpm_macro(os_data, requirements)
    disable_deltarpm()
    return DNF(loglevel)
