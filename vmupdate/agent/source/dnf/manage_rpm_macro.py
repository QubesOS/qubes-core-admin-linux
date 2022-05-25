import os
import pkg_resources

from typing import Dict


def manage_rpm_macro(os_data, requirements: Dict[str, str]):
    rpm_macro = "/usr/lib/rpm/macros.d/macros.qubes"
    if (os_data["id"] == "fedora"
            and os_data["release"] < pkg_resources.parse_version("33")):
        with open(rpm_macro, "w") as f:
            f.write("# CVE-2021-20271 mitigation\n"
                    "%_pkgverify_level all")
    else:
        if os.path.exists(rpm_macro):
            os.remove(rpm_macro)
        requirements.update({"dnf": "4.7.0", "rpm": "4.14.2"})
