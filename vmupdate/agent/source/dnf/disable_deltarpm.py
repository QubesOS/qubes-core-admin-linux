def disable_deltarpm(dnf_conf="/etc/dnf/dnf.conf"):
    """

    :param dnf_conf:
    :return:
    """
    # TODO dnf makecache
    with open(dnf_conf, "a+") as f:  # TODO: do not append if present
        f.write("\ndeltarpm=False\n")
