/*
 * The Qubes OS Project, http://www.qubes-os.org
 *
 * Copyright (C) 2010  Rafal Wojtczuk  <rafal@invisiblethingslab.com>
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
 *
 */

#include <sys/socket.h>
#include <sys/un.h>
#include <stdio.h>
#include <getopt.h>
#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
#include <sys/wait.h>
#include <errno.h>
#include <assert.h>
#include "qrexec.h"
#include "libqrexec-utils.h"

// whether qrexec-client should replace ESC with _ before printing the output
int replace_esc_stdout = 0;
int replace_esc_stderr = 0;

#define VCHAN_BUFFER_SIZE 65536

int local_stdin_fd, local_stdout_fd;
pid_t local_pid = 0;
/* flag if this is "remote" end of service call. In this case swap STDIN/STDOUT
 * msg types and send exit code at the end */
int is_service = 0;
int child_exited = 0;

static int handle_agent_handshake(libvchan_t *vchan, int remote_send_first)
{
    struct msg_header hdr;
    struct peer_info info;
    int who = 0; // even - send to remote, odd - receive from remote

    while (who < 2) {
        if ((who+remote_send_first) & 1) {
            if (!read_vchan_all(vchan, &hdr, sizeof(hdr))) {
                perror("daemon handshake");
                return -1;
            }
            if (hdr.type != MSG_HELLO || hdr.len != sizeof(info)) {
                fprintf(stderr, "Invalid daemon MSG_HELLO\n");
                return -1;
            }
            if (!read_vchan_all(vchan, &info, sizeof(info))) {
                perror("daemon handshake");
                return -1;
            }

            if (info.version != QREXEC_PROTOCOL_VERSION) {
                fprintf(stderr, "Incompatible daemon protocol version "
                        "(daemon %d, client %d)\n",
                        info.version, QREXEC_PROTOCOL_VERSION);
                return -1;
            }
        } else {
            hdr.type = MSG_HELLO;
            hdr.len = sizeof(info);
            info.version = QREXEC_PROTOCOL_VERSION;

            if (!write_vchan_all(vchan, &hdr, sizeof(hdr))) {
                fprintf(stderr, "Failed to send MSG_HELLO hdr to daemon\n");
                return -1;
            }
            if (!write_vchan_all(vchan, &info, sizeof(info))) {
                fprintf(stderr, "Failed to send MSG_HELLO to daemon\n");
                return -1;
            }
        }
        who++;
    }
    return 0;
}

static int handle_daemon_handshake(int fd)
{
    struct msg_header hdr;
    struct peer_info info;

    /* daemon send MSG_HELLO first */
    if (!read_all(fd, &hdr, sizeof(hdr))) {
        perror("daemon handshake");
        return -1;
    }
    if (hdr.type != MSG_HELLO || hdr.len != sizeof(info)) {
        fprintf(stderr, "Invalid daemon MSG_HELLO\n");
        return -1;
    }
    if (!read_all(fd, &info, sizeof(info))) {
        perror("daemon handshake");
        return -1;
    }

    if (info.version != QREXEC_PROTOCOL_VERSION) {
        fprintf(stderr, "Incompatible daemon protocol version "
                "(daemon %d, client %d)\n",
                info.version, QREXEC_PROTOCOL_VERSION);
        return -1;
    }

    hdr.type = MSG_HELLO;
    hdr.len = sizeof(info);
    info.version = QREXEC_PROTOCOL_VERSION;

    if (!write_all(fd, &hdr, sizeof(hdr))) {
        fprintf(stderr, "Failed to send MSG_HELLO hdr to daemon\n");
        return -1;
    }
    if (!write_all(fd, &info, sizeof(info))) {
        fprintf(stderr, "Failed to send MSG_HELLO to daemon\n");
        return -1;
    }
    return 0;
}

static int connect_unix_socket(const char *domname)
{
    int s, len;
    struct sockaddr_un remote;

    if ((s = socket(AF_UNIX, SOCK_STREAM, 0)) == -1) {
        perror("socket");
        return -1;
    }

    remote.sun_family = AF_UNIX;
    snprintf(remote.sun_path, sizeof remote.sun_path,
            QREXEC_DAEMON_SOCKET_DIR "/qrexec.%s", domname);
    len = strlen(remote.sun_path) + sizeof(remote.sun_family);
    if (connect(s, (struct sockaddr *) &remote, len) == -1) {
        perror("connect");
        exit(1);
    }
    if (handle_daemon_handshake(s) < 0)
        exit(1);
    return s;
}

static void sigchld_handler(int x __attribute__((__unused__)))
{
    child_exited = 1;
    signal(SIGCHLD, sigchld_handler);
}

/* called from do_fork_exec */
void do_exec(const char *prog)
{
    execl("/bin/bash", "bash", "-c", prog, NULL);
}

static void do_exit(int code)
{
    int status;
    // sever communication lines; wait for child, if any
    // so that qrexec-daemon can count (recursively) spawned processes correctly
    close(local_stdin_fd);
    close(local_stdout_fd);
    waitpid(-1, &status, 0);
    exit(code);
}


static void prepare_local_fds(char *cmdline)
{
    if (!cmdline) {
        local_stdin_fd = 1;
        local_stdout_fd = 0;
        return;
    }
    signal(SIGCHLD, sigchld_handler);
    do_fork_exec(cmdline, &local_pid, &local_stdin_fd, &local_stdout_fd,
            NULL);
}

/* ask the daemon to allocate vchan port */
static void negotiate_connection_params(int s, int other_domid, unsigned type,
        void *cmdline_param, int cmdline_size,
        int *data_domain, int *data_port)
{
    struct msg_header hdr;
    struct exec_params params;
    hdr.type = type;
    hdr.len = sizeof(params) + cmdline_size;
    params.connect_domain = other_domid;
    params.connect_port = 0;
    if (!write_all(s, &hdr, sizeof(hdr))
            || !write_all(s, &params, sizeof(params))
            || !write_all(s, cmdline_param, cmdline_size)) {
        perror("write daemon");
        do_exit(1);
    }
    /* the daemon will respond with the same message with connect_port filled
     * and empty cmdline */
    if (!read_all(s, &hdr, sizeof(hdr))) {
        perror("read daemon");
        do_exit(1);
    }
    assert(hdr.type == type);
    if (hdr.len != sizeof(params)) {
        fprintf(stderr, "Invalid response for 0x%x\n", type);
        do_exit(1);
    }
    if (!read_all(s, &params, sizeof(params))) {
        perror("read daemon");
        do_exit(1);
    }
    *data_port = params.connect_port;
    *data_domain = params.connect_domain;
}

static void send_service_connect(int s, char *conn_ident,
        int connect_domain, int connect_port)
{
    struct msg_header hdr;
    struct exec_params exec_params;
    struct service_params srv_params;

    hdr.type = MSG_SERVICE_CONNECT;
    hdr.len = sizeof(exec_params) + sizeof(srv_params);

    exec_params.connect_domain = connect_domain;
    exec_params.connect_port = connect_port;
    strncpy(srv_params.ident, conn_ident, sizeof(srv_params.ident));

    if (!write_all(s, &hdr, sizeof(hdr))
            || !write_all(s, &exec_params, sizeof(exec_params))
            || !write_all(s, &srv_params, sizeof(srv_params))) {
        perror("write daemon");
        do_exit(1);
    }
}

static void send_exit_code(libvchan_t *vchan, int status)
{
    struct msg_header hdr;

    hdr.type = MSG_DATA_EXIT_CODE;
    hdr.len = sizeof(int);
    if (libvchan_send(vchan, &hdr, sizeof(hdr)) != sizeof(hdr)) {
        fprintf(stderr, "Failed to write exit code to the agent\n");
        do_exit(1);
    }
    if (libvchan_send(vchan, &status, sizeof(status)) != sizeof(status)) {
        fprintf(stderr, "Failed to write exit code(2) to the agent\n");
        do_exit(1);
    }
}

static void handle_input(libvchan_t *vchan)
{
    char buf[MAX_DATA_CHUNK];
    int ret;
    struct msg_header hdr;

    ret = read(local_stdout_fd, buf, sizeof(buf));
    if (ret < 0) {
        perror("read");
        do_exit(1);
    }
    hdr.type = is_service ? MSG_DATA_STDOUT : MSG_DATA_STDIN;
    hdr.len = ret;
    if (libvchan_send(vchan, &hdr, sizeof(hdr)) != sizeof(hdr)) {
        fprintf(stderr, "Failed to write STDIN data to the agent\n");
        do_exit(1);
    }
    if (ret == 0) {
        close(local_stdout_fd);
        local_stdout_fd = -1;
        if (local_stdin_fd == -1) {
            // if not a remote end of service call, wait for exit status
            if (is_service) {
                // if pipe in opposite direction already closed, no need to stay alive
                if (local_pid == 0) {
                    /* if this is "remote" service end and no real local process
                     * exists (using own stdin/out) send also fake exit code */
                    send_exit_code(vchan, 0);
                }
                do_exit(0);
            }
        }
    }
    if (!write_vchan_all(vchan, buf, ret)) {
        if (!libvchan_is_open(vchan)) {
            // agent disconnected its end of socket, so no future data will be
            // send there; there is no sense to read from child stdout
            //
            // since vchan socket is buffered it doesn't mean all data was
            // received from the agent
            close(local_stdout_fd);
            local_stdout_fd = -1;
            if (local_stdin_fd == -1) {
                // since child does no longer accept data on its stdin, doesn't
                // make sense to process the data from the daemon
                //
                // we don't know real exit VM process code (exiting here, before
                // MSG_DATA_EXIT_CODE message)
                do_exit(1);
            }
        } else
            perror("write agent");
    }
}

void do_replace_esc(char *buf, int len) {
	int i;

	for (i = 0; i < len; i++)
		if (buf[i] == '\033')
			buf[i] = '_';
}

static void handle_vchan_data(libvchan_t *vchan)
{
    int status;
    struct msg_header hdr;
    char buf[MAX_DATA_CHUNK];

    if (libvchan_recv(vchan, &hdr, sizeof hdr) < 0) {
        perror("read vchan");
        do_exit(1);
    }
    if (hdr.len > MAX_DATA_CHUNK) {
        fprintf(stderr, "client_header.len=%d\n", hdr.len);
        do_exit(1);
    }
    if (!read_vchan_all(vchan, buf, hdr.len)) {
        perror("read daemon");
        do_exit(1);
    }

    switch (hdr.type) {
        /* both directions because we can serve as either end of service call */
        case MSG_DATA_STDIN:
        case MSG_DATA_STDOUT:
            if (local_stdin_fd == -1)
                break;
            if (replace_esc_stdout)
                do_replace_esc(buf, hdr.len);
            if (hdr.len == 0) {
                close(local_stdin_fd);
                local_stdin_fd = -1;
            } else if (!write_all(local_stdin_fd, buf, hdr.len)) {
                if (errno == EPIPE) {
                    // remote side have closed its stdin, handle data in oposite
                    // direction (if any) before exit
                    local_stdin_fd = -1;
                } else {
                    perror("write local stdout");
                    do_exit(1);
                }
            }
            break;
        case MSG_DATA_STDERR:
            if (replace_esc_stderr)
                do_replace_esc(buf, hdr.len);
            write_all(2, buf, hdr.len);
            break;
        case MSG_DATA_EXIT_CODE:
            libvchan_close(vchan);
            status = *(unsigned int *) buf;
            do_exit(status);
            break;
        default:
            fprintf(stderr, "unknown msg %d\n", hdr.type);
            do_exit(1);
    }
}

static void check_child_status(libvchan_t *vchan)
{
    pid_t pid;
    int status;

    pid = waitpid(local_pid, &status, WNOHANG);
    if (pid < 0) {
        perror("waitpid");
        do_exit(1);
    }
    if (pid == 0 || !WIFEXITED(status))
        return;
    if (is_service)
        send_exit_code(vchan, WEXITSTATUS(status));
    do_exit(status);
}

static void select_loop(libvchan_t *vchan)
{
    fd_set select_set;
    int max_fd;
    int ret;
    int vchan_fd;
    sigset_t selectmask;
    struct timespec zero_timeout = { 0, 0 };
    struct timespec select_timeout = { 10, 0 };

    sigemptyset(&selectmask);
    sigaddset(&selectmask, SIGCHLD);
    sigprocmask(SIG_BLOCK, &selectmask, NULL);
    sigemptyset(&selectmask);

    for (;;) {
        vchan_fd = libvchan_fd_for_select(vchan);
        FD_ZERO(&select_set);
        FD_SET(vchan_fd, &select_set);
        max_fd = vchan_fd;
        if (local_stdout_fd != -1 && libvchan_buffer_space(vchan)) {
            FD_SET(local_stdout_fd, &select_set);
            if (local_stdout_fd > max_fd)
                max_fd = local_stdout_fd;
        }
        if (child_exited)
            check_child_status(vchan);
        if (libvchan_data_ready(vchan) > 0) {
            /* check for other FDs, but exit immediately */
            ret = pselect(max_fd + 1, &select_set, NULL, NULL,
                    &zero_timeout, &selectmask);
        } else
            ret = pselect(max_fd + 1, &select_set, NULL, NULL,
                    &select_timeout, &selectmask);
        if (ret < 0) {
            if (errno == EINTR && local_pid > 0) {
                continue;
            } else {
                perror("select");
                do_exit(1);
            }
        }
        if (ret == 0) {
            if (!libvchan_is_open(vchan)) {
                /* remote disconnected witout a proper signaling */
                do_exit(1);
            }
        }
        if (FD_ISSET(vchan_fd, &select_set))
            libvchan_wait(vchan);
        while (libvchan_data_ready(vchan))
            handle_vchan_data(vchan);

        if (local_stdout_fd != -1
                && FD_ISSET(local_stdout_fd, &select_set))
            handle_input(vchan);
    }
}

static void usage(char *name)
{
    fprintf(stderr,
            "usage: %s [-t] [-T] -d domain_name ["
            "-l local_prog|"
            "-c request_id,src_domain_name,src_domain_id|"
            "-e] remote_cmdline\n"
            "-e means exit after sending cmd,\n"
            "-t enables replacing ESC character with '_' in command output, -T is the same for stderr\n"
            "-c: connect to existing process (response to trigger service call)\n",
            name);
    exit(1);
}

static void parse_connect(char *str, char **request_id,
        char **src_domain_name, int *src_domain_id)
{
    int i=0;
    char *token = NULL;
    char *separators = ",";

    token = strtok(str, separators);
    while (token)
    {
        switch (i)
        {
            case 0:
                *request_id = token;
                if (strlen(*request_id) >= sizeof(struct service_params)) {
                    fprintf(stderr, "Invalid -c parameter (request_id too long, max %lu)\n",
                            sizeof(struct service_params)-1);
                    exit(1);
                }
                break;
            case 1:
                *src_domain_name = token;
                break;
            case 2:
                *src_domain_id = atoi(token);
                break;
            default:
                fprintf(stderr, "Invalid -c parameter (should be: \"-c request_id,src_domain_name,src_domain_id\")\n");
                exit(1);
        }
        token = strtok(NULL, separators);
        i++;
    }
}

int main(int argc, char **argv)
{
    int opt;
    char *domname = NULL;
    libvchan_t *data_vchan = NULL;
    int data_port;
    int data_domain;
    int msg_type;
    int s;
    int just_exec = 0;
    int connect_existing = 0;
    char *local_cmdline = NULL;
    char *remote_cmdline = NULL;
    char *request_id;
    char *src_domain_name;
    int src_domain_id = 0; /* if not -c given, the process is run in dom0 */
    struct service_params svc_params;
    while ((opt = getopt(argc, argv, "d:l:ec:tT")) != -1) {
        switch (opt) {
            case 'd':
                domname = strdup(optarg);
                break;
            case 'l':
                local_cmdline = strdup(optarg);
                break;
            case 'e':
                just_exec = 1;
                break;
            case 'c':
                parse_connect(optarg, &request_id, &src_domain_name, &src_domain_id);
                connect_existing = 1;
                is_service = 1;
                break;
            case 't':
                replace_esc_stdout = 1;
                break;
            case 'T':
                replace_esc_stderr = 1;
                break;
            default:
                usage(argv[0]);
        }
    }
    if (optind >= argc || !domname)
        usage(argv[0]);
    remote_cmdline = argv[optind];

    register_exec_func(&do_exec);

    if (just_exec + connect_existing + (local_cmdline != 0) > 1) {
        fprintf(stderr, "ERROR: only one of -e, -l, -c can be specified\n");
        usage(argv[0]);
    }

    if (strcmp(domname, "dom0") == 0 && !connect_existing) {
        fprintf(stderr, "ERROR: when target domain is 'dom0', -c must be specified\n");
        usage(argv[0]);
    }

    if (strcmp(domname, "dom0") == 0) {
        if (connect_existing) {
            msg_type = MSG_SERVICE_CONNECT;
            strncpy(svc_params.ident, request_id, sizeof(svc_params.ident));
        } else if (just_exec)
            msg_type = MSG_JUST_EXEC;
        else
            msg_type = MSG_EXEC_CMDLINE;
        setenv("QREXEC_REMOTE_DOMAIN", src_domain_name, 1);
        s = connect_unix_socket(src_domain_name);
        negotiate_connection_params(s,
                0, /* dom0 */
                msg_type,
                connect_existing ? (void*)&svc_params : (void*)remote_cmdline,
                connect_existing ? sizeof(svc_params) : strlen(remote_cmdline) + 1,
                &data_domain,
                &data_port);

        prepare_local_fds(remote_cmdline);
        if (connect_existing)
            data_vchan = libvchan_client_init(data_domain, data_port);
        else {
            data_vchan = libvchan_server_init(data_domain, data_port,
                    VCHAN_BUFFER_SIZE, VCHAN_BUFFER_SIZE);
            while (data_vchan && !libvchan_is_open(data_vchan))
                libvchan_wait(data_vchan);
        }
        if (!data_vchan) {
            fprintf(stderr, "Failed to open data vchan connection\n");
            do_exit(1);
        }
        if (handle_agent_handshake(data_vchan, connect_existing) < 0)
            do_exit(1);
        select_loop(data_vchan);
    } else {
        if (just_exec)
            msg_type = MSG_JUST_EXEC;
        else
            msg_type = MSG_EXEC_CMDLINE;
        s = connect_unix_socket(domname);
        negotiate_connection_params(s,
                src_domain_id,
                msg_type,
                remote_cmdline,
                strlen(remote_cmdline) + 1,
                &data_domain,
                &data_port);
        close(s);
        setenv("QREXEC_REMOTE_DOMAIN", domname, 1);
        prepare_local_fds(local_cmdline);
        if (connect_existing) {
            s = connect_unix_socket(src_domain_name);
            send_service_connect(s, request_id, data_domain, data_port);
            close(s);
        } else {
            data_vchan = libvchan_server_init(data_domain, data_port,
                    VCHAN_BUFFER_SIZE, VCHAN_BUFFER_SIZE);
            if (!data_vchan) {
                fprintf(stderr, "Failed to start data vchan server\n");
                do_exit(1);
            }
            while (!libvchan_is_open(data_vchan))
                libvchan_wait(data_vchan);
            if (handle_agent_handshake(data_vchan, 0) < 0)
                do_exit(1);
            select_loop(data_vchan);
        }
    }
    return 0;
}

// vim:ts=4:sw=4:et:
