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

#include <sys/select.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <string.h>
#include <assert.h>
#include "qrexec.h"
#include "libqrexec-utils.h"

enum client_state {
    CLIENT_INVALID = 0,	// table slot not used
    CLIENT_HELLO, // waiting for client hello
    CLIENT_CMDLINE,	// waiting for cmdline from client
    CLIENT_RUNNING // waiting for client termination (to release vchan port)
};

enum vchan_port_state {
    VCHAN_PORT_UNUSED = -1
};

struct _client {
    int state;		// enum client_state
};

struct _policy_pending {
    pid_t pid;
    struct service_params params;
    int reserved_vchan_port;
};

#define VCHAN_BASE_DATA_PORT (VCHAN_BASE_PORT+1)

/*
   The "clients" array is indexed by client's fd.
   Thus its size must be equal MAX_FDS; defining MAX_CLIENTS for clarity.
   */

#define MAX_CLIENTS MAX_FDS
struct _client clients[MAX_CLIENTS];	// data on all qrexec_client connections

struct _policy_pending policy_pending[MAX_CLIENTS];
int policy_pending_max = -1;

/* indexed with vchan port number relative to VCHAN_BASE_DATA_PORT; stores
 * either VCHAN_PORT_* or remote domain id for used port */
int used_vchan_ports[MAX_CLIENTS];

/* notify client (close its connection) when connection initiated by it was
 * terminated - used by qrexec-policy to cleanup (disposable) VM; indexed with
 * vchan port number relative to VCHAN_BASE_DATA_PORT; stores fd of given
 * client or -1 if none requested */
int vchan_port_notify_client[MAX_CLIENTS];

int max_client_fd = -1;		// current max fd of all clients; so that we need not to scan all the "clients" table
int qrexec_daemon_unix_socket_fd;	// /var/run/qubes/qrexec.xid descriptor
const char *default_user = "user";
const char default_user_keyword[] = "DEFAULT:";
#define default_user_keyword_len_without_colon (sizeof(default_user_keyword)-2)

int opt_quiet = 0;

#ifdef __GNUC__
#  define UNUSED(x) UNUSED_ ## x __attribute__((__unused__))
#else
#  define UNUSED(x) UNUSED_ ## x
#endif

volatile int children_count;

libvchan_t *vchan;

void sigusr1_handler(int UNUSED(x))
{
    if (!opt_quiet)
        fprintf(stderr, "connected\n");
    exit(0);
}

void sigchld_parent_handler(int UNUSED(x))
{
    children_count--;
    /* starting value is 0 so we see dead real qrexec-daemon as -1 */
    if (children_count < 0) {
        if (!opt_quiet)
            fprintf(stderr, "failed\n");
        else
            fprintf(stderr, "Connection to the VM failed\n");
        exit(1);
    }
}

static void sigchld_handler(int UNUSED(x));

char *remote_domain_name;	// guess what
int remote_domain_id;

void unlink_qrexec_socket()
{
    char socket_address[40];
    char link_to_socket_name[strlen(remote_domain_name) + sizeof(socket_address)];

    snprintf(socket_address, sizeof(socket_address),
            QREXEC_DAEMON_SOCKET_DIR "/qrexec.%d", remote_domain_id);
    snprintf(link_to_socket_name, sizeof link_to_socket_name,
            QREXEC_DAEMON_SOCKET_DIR "/qrexec.%s", remote_domain_name);
    unlink(socket_address);
    unlink(link_to_socket_name);
}

void handle_vchan_error(const char *op)
{
    fprintf(stderr, "Error while vchan %s, exiting\n", op);
    exit(1);
}


int create_qrexec_socket(int domid, const char *domname)
{
    char socket_address[40];
    char link_to_socket_name[strlen(domname) + sizeof(socket_address)];

    snprintf(socket_address, sizeof(socket_address),
            QREXEC_DAEMON_SOCKET_DIR "/qrexec.%d", domid);
    snprintf(link_to_socket_name, sizeof link_to_socket_name,
            QREXEC_DAEMON_SOCKET_DIR "/qrexec.%s", domname);
    unlink(link_to_socket_name);
    if (symlink(socket_address, link_to_socket_name)) {
        fprintf(stderr, "symlink(%s,%s) failed: %s\n", socket_address,
                link_to_socket_name, strerror (errno));
    }
    atexit(unlink_qrexec_socket);
    return get_server_socket(socket_address);
}

#define MAX_STARTUP_TIME_DEFAULT 60

static void incompatible_protocol_error_message(
        const char *domain_name, int remote_version)
{
    char text[1024];
    int ret;
    struct stat buf;
    ret=stat("/usr/bin/kdialog", &buf);
#define KDIALOG_CMD "kdialog --title 'Qrexec daemon' --sorry "
#define ZENITY_CMD "zenity --title 'Qrexec daemon' --warning --text "
    snprintf(text, sizeof(text),
            "%s"
            "'Domain %s uses incompatible qrexec protocol (%d instead of %d). "
            "You need to update either dom0 or VM packages.\n"
            "To access this VM console do not close this error message and run:\n"
            "sudo xl console -t pv %s'",
            ret==0 ? KDIALOG_CMD : ZENITY_CMD,
            domain_name, remote_version, QREXEC_PROTOCOL_VERSION, domain_name);
#undef KDIALOG_CMD
#undef ZENITY_CMD
    system(text);
}

int handle_agent_hello(libvchan_t *ctrl, const char *domain_name)
{
    struct msg_header hdr;
    struct peer_info info;

    if (libvchan_recv(ctrl, &hdr, sizeof(hdr)) != sizeof(hdr)) {
        fprintf(stderr, "Failed to read agent HELLO hdr\n");
        return -1;
    }

    if (hdr.type != MSG_HELLO || hdr.len != sizeof(info)) {
        fprintf(stderr, "Invalid HELLO packet received: type %d, len %d\n", hdr.type, hdr.len);
        return -1;
    }

    if (libvchan_recv(ctrl, &info, sizeof(info)) != sizeof(info)) {
        fprintf(stderr, "Failed to read agent HELLO body\n");
        return -1;
    }

    if (info.version != QREXEC_PROTOCOL_VERSION) {
        fprintf(stderr, "Incompatible agent protocol version (remote %d, local %d)\n", info.version, QREXEC_PROTOCOL_VERSION);
        incompatible_protocol_error_message(domain_name, info.version);
        return -1;
    }

    /* send own HELLO */
    /* those messages are the same as received from agent, but set it again for
     * readability */
    hdr.type = MSG_HELLO;
    hdr.len = sizeof(info);
    info.version = QREXEC_PROTOCOL_VERSION;

    if (libvchan_send(ctrl, &hdr, sizeof(hdr)) != sizeof(hdr)) {
        fprintf(stderr, "Failed to send HELLO hdr to agent\n");
        return -1;
    }

    if (libvchan_send(ctrl, &info, sizeof(info)) != sizeof(info)) {
        fprintf(stderr, "Failed to send HELLO hdr to agent\n");
        return -1;
    }

    return 0;
}

/* do the preparatory tasks, needed before entering the main event loop */
void init(int xid)
{
    char qrexec_error_log_name[256];
    int logfd;
    int i;
    pid_t pid;
    int startup_timeout = MAX_STARTUP_TIME_DEFAULT;
    const char *startup_timeout_str = NULL;

    if (xid <= 0) {
        fprintf(stderr, "domain id=0?\n");
        exit(1);
    }
    startup_timeout_str = getenv("QREXEC_STARTUP_TIMEOUT");
    if (startup_timeout_str) {
        startup_timeout = atoi(startup_timeout_str);
        if (startup_timeout <= 0)
            // invalid or negative number
            startup_timeout = MAX_STARTUP_TIME_DEFAULT;
    }
    signal(SIGUSR1, sigusr1_handler);
    signal(SIGCHLD, sigchld_parent_handler);
    switch (pid=fork()) {
    case -1:
        perror("fork");
        exit(1);
    case 0:
        break;
    default:
        if (getenv("QREXEC_STARTUP_NOWAIT"))
            exit(0);
        if (!opt_quiet)
            fprintf(stderr, "Waiting for VM's qrexec agent.");
        for (i=0;i<startup_timeout;i++) {
            sleep(1);
            if (!opt_quiet)
                fprintf(stderr, ".");
            if (i==startup_timeout-1) {
                break;
            }
        }
        fprintf(stderr, "Cannot connect to '%s' qrexec agent for %d seconds, giving up\n", remote_domain_name, startup_timeout);
        exit(3);
    }
    close(0);
    snprintf(qrexec_error_log_name, sizeof(qrexec_error_log_name),
         "/var/log/qubes/qrexec.%s.log", remote_domain_name);
    umask(0007);        // make the log readable by the "qubes" group
    logfd =
        open(qrexec_error_log_name, O_WRONLY | O_CREAT | O_TRUNC,
         0660);

    if (logfd < 0) {
        perror("open");
        exit(1);
    }

    dup2(logfd, 1);
    dup2(logfd, 2);

    chdir("/var/run/qubes");
    if (setsid() < 0) {
        perror("setsid()");
        exit(1);
    }

    vchan = libvchan_client_init(xid, VCHAN_BASE_PORT);
    if (!vchan) {
        perror("cannot connect to qrexec agent");
        exit(1);
    }
    if (handle_agent_hello(vchan, remote_domain_name) < 0) {
        exit(1);
    }

    if (setgid(getgid()) < 0) {
        perror("setgid()");
        exit(1);
    }
    if (setuid(getuid()) < 0) {
        perror("setuid()");
        exit(1);
    }

    /* initialize clients state arrays */
    for (i = 0; i < MAX_CLIENTS; i++) {
        clients[i].state = CLIENT_INVALID;
        policy_pending[i].pid = 0;
        used_vchan_ports[i] = VCHAN_PORT_UNUSED;
        vchan_port_notify_client[i] = VCHAN_PORT_UNUSED;
    }

    /* When running as root, make the socket accessible; perms on /var/run/qubes still apply */
    umask(0);
    qrexec_daemon_unix_socket_fd =
        create_qrexec_socket(xid, remote_domain_name);
    umask(0077);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGCHLD, sigchld_handler);
    signal(SIGUSR1, SIG_DFL);
    kill(getppid(), SIGUSR1);   // let the parent know we are ready
}

static int send_client_hello(int fd)
{
    struct msg_header hdr;
    struct peer_info info;

    hdr.type = MSG_HELLO;
    hdr.len = sizeof(info);
    info.version = QREXEC_PROTOCOL_VERSION;

    if (!write_all(fd, &hdr, sizeof(hdr))) {
        fprintf(stderr, "Failed to send MSG_HELLO hdr to client %d\n", fd);
        return -1;
    }
    if (!write_all(fd, &info, sizeof(info))) {
        fprintf(stderr, "Failed to send MSG_HELLO to client %d\n", fd);
        return -1;
    }
    return 0;
}

static int allocate_vchan_port(int new_state)
{
    int i;

    for (i = 0; i < MAX_CLIENTS; i++) {
        if (used_vchan_ports[i] == VCHAN_PORT_UNUSED) {
            used_vchan_ports[i] = new_state;
            return VCHAN_BASE_DATA_PORT+i;
        }
    }
    return -1;
}

static void handle_new_client()
{
    int fd = do_accept(qrexec_daemon_unix_socket_fd);
    if (fd >= MAX_CLIENTS) {
        fprintf(stderr, "too many clients ?\n");
        exit(1);
    }

    if (send_client_hello(fd) < 0) {
        close(fd);
        clients[fd].state = CLIENT_INVALID;
        return;
    }

    clients[fd].state = CLIENT_HELLO;
    if (fd > max_client_fd)
        max_client_fd = fd;
}

static void terminate_client(int fd)
{
    int port;
    clients[fd].state = CLIENT_INVALID;
    close(fd);
    /* if client requested vchan connection end notify, cancel it */
    for (port = 0; port < MAX_CLIENTS; port++) {
        if (vchan_port_notify_client[port] == fd)
            vchan_port_notify_client[port] = VCHAN_PORT_UNUSED;
    }
}

static void release_vchan_port(int port, int expected_remote_id)
{
    /* release only if was reserved for connection to given domain */
    if (used_vchan_ports[port-VCHAN_BASE_DATA_PORT] == expected_remote_id) {
        used_vchan_ports[port-VCHAN_BASE_DATA_PORT] = VCHAN_PORT_UNUSED;
        /* notify client if requested - it will clear notification request */
        if (vchan_port_notify_client[port-VCHAN_BASE_DATA_PORT] != VCHAN_PORT_UNUSED)
            terminate_client(vchan_port_notify_client[port-VCHAN_BASE_DATA_PORT]);
    }
}

static int handle_cmdline_body_from_client(int fd, struct msg_header *hdr)
{
    struct exec_params params;
    int len = hdr->len-sizeof(params);
    char buf[len];
    int use_default_user = 0;
    int i;

    if (!read_all(fd, &params, sizeof(params))) {
        terminate_client(fd);
        return 0;
    }
    if (!read_all(fd, buf, len)) {
        terminate_client(fd);
        return 0;
    }

    if (hdr->type == MSG_SERVICE_CONNECT) {
        /* if the service was accepted, do not send spurious
         * MSG_SERVICE_REFUSED when service process itself exit with non-zero
         * code */
        for (i = 0; i <= policy_pending_max; i++) {
            if (policy_pending[i].pid &&
                    strncmp(policy_pending[i].params.ident, buf, len) == 0) {
                policy_pending[i].pid = 0;
                while (policy_pending_max > 0 &&
                        policy_pending[policy_pending_max].pid == 0)
                    policy_pending_max--;
                break;
            }
        }
    }

    if (!params.connect_port) {
        struct exec_params client_params;
        /* allocate port and send it to the client */
        params.connect_port = allocate_vchan_port(params.connect_domain);
        if (params.connect_port <= 0) {
            fprintf(stderr, "Failed to allocate new vchan port, too many clients?\n");
            terminate_client(fd);
            return 0;
        }
        /* notify the client when this connection got terminated */
        vchan_port_notify_client[params.connect_port-VCHAN_BASE_DATA_PORT] = fd;
        client_params.connect_port = params.connect_port;
        client_params.connect_domain = remote_domain_id;
        hdr->len = sizeof(client_params);
        if (!write_all(fd, hdr, sizeof(*hdr))) {
            terminate_client(fd);
            release_vchan_port(params.connect_port, params.connect_domain);
            return 0;
        }
        if (!write_all(fd, &client_params, sizeof(client_params))) {
            terminate_client(fd);
            release_vchan_port(params.connect_port, params.connect_domain);
            return 0;
        }
        /* restore original len value */
        hdr->len = len+sizeof(params);
    } else {
        assert(params.connect_port >= VCHAN_BASE_DATA_PORT);
        assert(params.connect_port < VCHAN_BASE_DATA_PORT+MAX_CLIENTS);
    }

    if (!strncmp(buf, default_user_keyword, default_user_keyword_len_without_colon+1)) {
        use_default_user = 1;
        hdr->len -= default_user_keyword_len_without_colon;
        hdr->len += strlen(default_user);
    }
    if (libvchan_send(vchan, hdr, sizeof(*hdr)) < 0)
        handle_vchan_error("send");
    if (libvchan_send(vchan, &params, sizeof(params)) < 0)
        handle_vchan_error("send params");
    if (use_default_user) {
        if (libvchan_send(vchan, default_user, strlen(default_user)) < 0)
            handle_vchan_error("send default_user");
        if (libvchan_send(vchan, buf+default_user_keyword_len_without_colon,
                    len-default_user_keyword_len_without_colon) < 0)
            handle_vchan_error("send buf");
    } else
        if (libvchan_send(vchan, buf, len) < 0)
            handle_vchan_error("send buf");
    return 1;
}

static void handle_cmdline_message_from_client(int fd)
{
    struct msg_header hdr;
    if (!read_all(fd, &hdr, sizeof hdr)) {
        terminate_client(fd);
        return;
    }
    switch (hdr.type) {
        case MSG_EXEC_CMDLINE:
        case MSG_JUST_EXEC:
        case MSG_SERVICE_CONNECT:
            break;
        default:
            terminate_client(fd);
            return;
    }

    if (!handle_cmdline_body_from_client(fd, &hdr))
        // client disconnected while sending cmdline, above call already
        // cleaned up client info
        return;
    clients[fd].state = CLIENT_RUNNING;
}

static void handle_client_hello(int fd)
{
    struct msg_header hdr;
    struct peer_info info;

    if (!read_all(fd, &hdr, sizeof hdr)) {
        terminate_client(fd);
        return;
    }
    if (hdr.type != MSG_HELLO || hdr.len != sizeof(info)) {
        fprintf(stderr, "Invalid HELLO packet received from client %d: "
                "type %d, len %d\n", fd, hdr.type, hdr.len);
        terminate_client(fd);
        return;
    }
    if (!read_all(fd, &info, sizeof info)) {
        terminate_client(fd);
        return;
    }
    if (info.version != QREXEC_PROTOCOL_VERSION) {
        fprintf(stderr, "Incompatible client protocol version (remote %d, local %d)\n", info.version, QREXEC_PROTOCOL_VERSION);
        terminate_client(fd);
        return;
    }
    clients[fd].state = CLIENT_CMDLINE;
}

/* handle data received from one of qrexec_client processes */
static void handle_message_from_client(int fd)
{
    char buf[MAX_DATA_CHUNK];

    switch (clients[fd].state) {
        case CLIENT_HELLO:
            handle_client_hello(fd);
            return;
        case CLIENT_CMDLINE:
            handle_cmdline_message_from_client(fd);
            return;
        case CLIENT_RUNNING:
            // expected EOF
            if (read(fd, buf, sizeof(buf)) != 0) {
                fprintf(stderr, "Unexpected data received from client %d\n", fd);
            }
            terminate_client(fd);
            return;
        default:
            fprintf(stderr, "Invalid client state %d\n", clients[fd].state);
            exit(1);
    }
}


/*
 * The signal handler executes asynchronously; therefore all it should do is
 * to set a flag "signal has arrived", and let the main even loop react to this
 * flag in appropriate moment.
 */

int child_exited;

static void sigchld_handler(int UNUSED(x))
{
    child_exited = 1;
    signal(SIGCHLD, sigchld_handler);
}

static void send_service_refused(libvchan_t *vchan, struct service_params *params) {
    struct msg_header hdr;

    hdr.type = MSG_SERVICE_REFUSED;
    hdr.len = sizeof(*params);

    if (libvchan_send(vchan, &hdr, sizeof(hdr)) != sizeof(hdr)) {
        fprintf(stderr, "Failed to send MSG_SERVICE_REFUSED hdr to agent\n");
        exit(1);
    }

    if (libvchan_send(vchan, params, sizeof(*params)) != sizeof(*params)) {
        fprintf(stderr, "Failed to send MSG_SERVICE_REFUSED to agent\n");
        exit(1);
    }
}

/* clean zombies, check for denied service calls */
static void reap_children()
{
    int status;
    int i;

    pid_t pid;
    while ((pid=waitpid(-1, &status, WNOHANG)) > 0) {
        for (i = 0; i <= policy_pending_max; i++) {
            if (policy_pending[i].pid == pid) {
                status = WEXITSTATUS(status);
                if (status != 0) {
                    send_service_refused(vchan, &policy_pending[i].params);
                }
                /* in case of allowed calls, we will do the rest in
                 * MSG_SERVICE_CONNECT from client handler */
                policy_pending[i].pid = 0;
                while (policy_pending_max > 0 &&
                        policy_pending[policy_pending_max].pid == 0)
                    policy_pending_max--;
                break;
            }
        }
    }
    child_exited = 0;
}

static int find_policy_pending_slot() {
    int i;

    for (i = 0; i < MAX_CLIENTS; i++) {
        if (policy_pending[i].pid == 0) {
            if (i > policy_pending_max)
                policy_pending_max = i;
            return i;
        }
    }
    return -1;
}

static void sanitize_name(char * untrusted_s_signed, char *extra_allowed_chars)
{
    unsigned char * untrusted_s;
    for (untrusted_s=(unsigned char*)untrusted_s_signed; *untrusted_s; untrusted_s++) {
        if (*untrusted_s >= 'a' && *untrusted_s <= 'z')
            continue;
        if (*untrusted_s >= 'A' && *untrusted_s <= 'Z')
            continue;
        if (*untrusted_s >= '0' && *untrusted_s <= '9')
            continue;
        if (*untrusted_s == '_' ||
               *untrusted_s == '-' ||
               *untrusted_s == '.')
            continue;
        if (extra_allowed_chars && strchr(extra_allowed_chars, *untrusted_s))
            continue;
        *untrusted_s = '_';
    }
}

#define ENSURE_NULL_TERMINATED(x) x[sizeof(x)-1] = 0

/*
 * Called when agent sends a message asking to execute a predefined command.
 */

static void handle_execute_service(void)
{
    int i;
    int policy_pending_slot;
    pid_t pid;
    struct trigger_service_params untrusted_params, params;
    char remote_domain_id_str[10];

    if (libvchan_recv(vchan, &untrusted_params, sizeof(untrusted_params)) < 0)
        handle_vchan_error("recv params");

    /* sanitize start */
    ENSURE_NULL_TERMINATED(untrusted_params.service_name);
    ENSURE_NULL_TERMINATED(untrusted_params.target_domain);
    ENSURE_NULL_TERMINATED(untrusted_params.request_id.ident);
    sanitize_name(untrusted_params.service_name, "+");
    sanitize_name(untrusted_params.target_domain, "@:");
    sanitize_name(untrusted_params.request_id.ident, " ");
    params = untrusted_params;
    /* sanitize end */

    policy_pending_slot = find_policy_pending_slot();
    if (policy_pending_slot < 0) {
        fprintf(stderr, "Service request denied, too many pending requests\n");
        send_service_refused(vchan, &untrusted_params.request_id);
        return;
    }

    switch (pid=fork()) {
        case -1:
            perror("fork");
            exit(1);
        case 0:
            break;
        default:
            policy_pending[policy_pending_slot].pid = pid;
            policy_pending[policy_pending_slot].params = untrusted_params.request_id;
            return;
    }
    for (i = 3; i < MAX_FDS; i++)
        close(i);
    signal(SIGCHLD, SIG_DFL);
    signal(SIGPIPE, SIG_DFL);
    snprintf(remote_domain_id_str, sizeof(remote_domain_id_str), "%d",
            remote_domain_id);
    execl("/usr/bin/qrexec-policy", "qrexec-policy", "--",
            remote_domain_id_str, remote_domain_name, params.target_domain,
            params.service_name, params.request_id.ident, NULL);
    perror("execl");
    _exit(1);
}

static void handle_connection_terminated()
{
    struct exec_params untrusted_params, params;

    if (libvchan_recv(vchan, &untrusted_params, sizeof(untrusted_params)) < 0)
        handle_vchan_error("recv params");
    /* sanitize start */
    if (untrusted_params.connect_port < VCHAN_BASE_DATA_PORT ||
            untrusted_params.connect_port >= VCHAN_BASE_DATA_PORT+MAX_CLIENTS) {
        fprintf(stderr, "Invalid port in MSG_CONNECTION_TERMINATED (%d)\n",
                untrusted_params.connect_port);
        exit(1);
    }
    /* untrusted_params.connect_domain even if invalid will not harm - in worst
     * case the port will not be released */
    params = untrusted_params;
    /* sanitize end */
    release_vchan_port(params.connect_port, params.connect_domain);
}

static void sanitize_message_from_agent(struct msg_header *untrusted_header)
{
    switch (untrusted_header->type) {
        case MSG_TRIGGER_SERVICE:
            if (untrusted_header->len != sizeof(struct trigger_service_params)) {
                fprintf(stderr, "agent sent invalid MSG_TRIGGER_SERVICE packet\n");
                exit(1);
            }
            break;
        case MSG_CONNECTION_TERMINATED:
            if (untrusted_header->len != sizeof(struct exec_params)) {
                fprintf(stderr, "agent sent invalid MSG_CONNECTION_TERMINATED packet\n");
                exit(1);
            }
            break;
        default:
            fprintf(stderr, "unknown mesage type 0x%x from agent\n",
                    untrusted_header->type);
            exit(1);
    }
}

static void handle_message_from_agent(void)
{
    struct msg_header hdr, untrusted_hdr;

    if (libvchan_recv(vchan, &untrusted_hdr, sizeof(untrusted_hdr)) < 0)
        handle_vchan_error("recv hdr");
    /* sanitize start */
    sanitize_message_from_agent(&untrusted_hdr);
    hdr = untrusted_hdr;
    /* sanitize end */

    //      fprintf(stderr, "got %x %x %x\n", hdr.type, hdr.client_id,
    //              hdr.len);

    switch (hdr.type) {
        case MSG_TRIGGER_SERVICE:
            handle_execute_service();
            return;
        case MSG_CONNECTION_TERMINATED:
            handle_connection_terminated();
            return;
    }
}

/*
 * Scan the "clients" table, add ones we want to read from (because the other
 * end has not send MSG_XOFF on them) to read_fdset, add ones we want to write
 * to (because its pipe is full) to write_fdset. Return the highest used file
 * descriptor number, needed for the first select() parameter.
 */
static int fill_fdsets_for_select(fd_set * read_fdset, fd_set * write_fdset)
{
    int i;
    int max = -1;
    FD_ZERO(read_fdset);
    FD_ZERO(write_fdset);
    for (i = 0; i <= max_client_fd; i++) {
        if (clients[i].state != CLIENT_INVALID) {
            FD_SET(i, read_fdset);
            max = i;
        }
    }
    FD_SET(qrexec_daemon_unix_socket_fd, read_fdset);
    if (qrexec_daemon_unix_socket_fd > max)
        max = qrexec_daemon_unix_socket_fd;
    return max;
}

int main(int argc, char **argv)
{
    fd_set read_fdset, write_fdset;
    int i, opt;
    int max;
    sigset_t chld_set;

    while ((opt=getopt(argc, argv, "q")) != -1) {
        switch (opt) {
            case 'q':
                opt_quiet = 1;
                break;
            default: /* '?' */
                fprintf(stderr, "usage: %s [-q] domainid domain-name [default user]\n", argv[0]);
                exit(1);
        }
    }
    if (argc - optind < 2 || argc - optind > 3) {
        fprintf(stderr, "usage: %s [-q] domainid domain-name [default user]\n", argv[0]);
        exit(1);
    }
    remote_domain_id = atoi(argv[optind]);
    remote_domain_name = argv[optind+1];
    if (argc - optind >= 3)
        default_user = argv[optind+2];
    init(remote_domain_id);
    sigemptyset(&chld_set);
    sigaddset(&chld_set, SIGCHLD);
    signal(SIGCHLD, sigchld_handler);
    /*
     * The main event loop. Waits for one of the following events:
     * - message from client
     * - message from agent
     * - new client
     * - child exited
     */
    for (;;) {
        max = fill_fdsets_for_select(&read_fdset, &write_fdset);
        if (libvchan_buffer_space(vchan) <= (int)sizeof(struct msg_header))
            FD_ZERO(&read_fdset);	// vchan full - don't read from clients

        sigprocmask(SIG_BLOCK, &chld_set, NULL);
        if (child_exited)
            reap_children();
        wait_for_vchan_or_argfd(vchan, max, &read_fdset, &write_fdset);
        sigprocmask(SIG_UNBLOCK, &chld_set, NULL);

        if (FD_ISSET(qrexec_daemon_unix_socket_fd, &read_fdset))
            handle_new_client();

        while (libvchan_data_ready(vchan))
            handle_message_from_agent();

        for (i = 0; i <= max_client_fd; i++)
            if (clients[i].state != CLIENT_INVALID
                && FD_ISSET(i, &read_fdset))
                handle_message_from_client(i);
    }
}

// vim:ts=4:sw=4:et:
