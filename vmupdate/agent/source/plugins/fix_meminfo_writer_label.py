import subprocess
import os
import signal


def fix_meminfo_writer_label(os_data, log, **kwargs):
    """
    Fix meminfo-writer SELinux label to make memory ballooning work again

    # https://github.com/QubesOS/qubes-issues/issues/9663
    """

    if os_data["id"] == "fedora":
        if os.path.exists("/usr/sbin/selinuxenabled"):
            if os.path.exists("/usr/bin/meminfo-writer"):
                meminfo_path = "/usr/bin/meminfo-writer"
            else:
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
                    # Disable SELinux for the update time, to avoid
                    # half-updated policy interrupting the process. This is
                    # workaround for
                    # https://bugzilla.redhat.com/show_bug.cgi?id=2380156
                    subprocess.check_call(["setenforce", "0"])
            except subprocess.CalledProcessError as e:
                log.error(f"Error processing {meminfo_path}: {e}")

            if label_changed:
                try:
                    with open("/run/meminfo-writer.pid", "r", encoding="utf-8") as f:
                        target_pid = int(f.read().strip())
                        os.kill(target_pid, signal.SIGUSR1)
                        log.info(
                            f"USR1 signal sent to meminfo-writer process id: {target_pid}"
                        )
                except (FileNotFoundError, ValueError, OSError) as e:
                    log.error(
                        f"Error sending USR1 signal to meminfo-writer process: {e}"
                    )
