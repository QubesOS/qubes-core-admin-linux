import subprocess
import os


def fix_meminfo_writer_label(os_data, log, **kwargs):
    """
    Fix meminfo-writer SELinux label to make memory ballooning work again

    # https://github.com/QubesOS/qubes-issues/issues/9663
    """

    if os_data["id"] == "fedora":
        if os.path.exists("/usr/sbin/selinuxenabled"):
            meminfo_path = "/usr/sbin/meminfo-writer"
            expected_label = "qubes_meminfo_writer_exec_t"

            label_changed = False
            try:
                if subprocess.call(["/usr/sbin/selinuxenabled"]) == 0:
                    output = subprocess.check_output(
                        ["ls", "-Z", meminfo_path], universal_newlines=True
                    )
                    if expected_label not in output:
                        subprocess.check_call(
                            ["chcon", "-t", expected_label, meminfo_path]
                        )
                        log.info(
                            f"SELinux label for {meminfo_path} changed to '{expected_label}'"
                        )
                        label_changed = True
            except subprocess.CalledProcessError as e:
                log.error(f"Error processing {meminfo_path}: {e}")

            if label_changed:
                try:
                    subprocess.check_call(
                        ["systemctl", "restart", "qubes-meminfo-writer"]
                    )
                    log.info("qubes-meminfo-writer service restarted")
                except subprocess.CalledProcessError as e:
                    log.error(f"Error restarting qubes-meminfo-writer service: {e}")
