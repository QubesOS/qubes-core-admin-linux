APT_CONF = "/etc/apt/apt.conf"


def allow_release_info_change(os_data, ):
    if os_data["codename"] == "buster":
        # https://bugs.debian.org/931566
        # Apply the workaround manually, to be able to pull in the fixed
        # apt version
        with open(APT_CONF, "a+") as f:  # TODO: do not append if present
            f.write('\nAcquire::AllowReleaseInfoChange "false";\n')
