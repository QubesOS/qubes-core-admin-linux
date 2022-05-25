from .allow_release_info_change import allow_release_info_change


def get_configured_apt(os_data, requirements, loglevel):
    try:
        from .apt_api import APT
    except ImportError:
        # no progress reporting
        from .apt_cli import APTCLI as APT

    allow_release_info_change(os_data)
    return APT(loglevel)
