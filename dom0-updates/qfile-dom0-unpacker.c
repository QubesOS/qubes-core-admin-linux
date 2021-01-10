#define _GNU_SOURCE
#include <grp.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <pwd.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <string.h>
#include <unistd.h>
#include <sys/fsuid.h>
#include <errno.h>
#include <limits.h>
#include <libqubes-rpc-filecopy.h>

#define DEFAULT_MAX_UPDATES_BYTES (4LL<<30)
#define DEFAULT_MAX_UPDATES_FILES 2048
 
#define min(a,b) ((a) < (b) ? (a) : (b))
#define max(a,b) ((a) > (b) ? (a) : (b))

int prepare_creds_return_uid(const char *username)
{
    struct passwd *pwd;
    // First try name
    pwd = getpwnam(username);
    if (!pwd) {
        // Then try UID
        pwd = getpwuid(atoi(username));
        if (!pwd) {
            perror("getpwuid");
            exit(1);
        }
    }
    setenv("HOME", pwd->pw_dir, 1);
    setenv("USER", pwd->pw_name, 1);
    if (setgid(pwd->pw_gid) < 0) {
        perror("setgid");
        exit(1);
    }
    initgroups(pwd->pw_name, pwd->pw_gid);
    if (setfsuid(pwd->pw_uid) < 0) {
        perror("setfsuid");
        exit(1);
    }
    return pwd->pw_uid;
}

int main(int argc, char ** argv)
{
    const char *incoming_dir;
    int uid;
    int i;
    char *var;
    long long files_limit = DEFAULT_MAX_UPDATES_FILES;
    long long bytes_limit = DEFAULT_MAX_UPDATES_BYTES;

    struct statvfs st;
    long long root_free_space;

    if (argc < 3) {
        fprintf(stderr, "Invalid parameters, usage: %s user dir [-v]\n", argv[0]);
        exit(1);
    }

    uid = prepare_creds_return_uid(argv[1]);

    incoming_dir = argv[2];
    mkdir(incoming_dir, 0700);
    if (chdir(incoming_dir)) {
        fprintf(stderr, "Error chdir to %s\n", incoming_dir);
        exit(1);
    }
    if (chroot(incoming_dir)) {//impossible
        fprintf(stderr, "Error chroot to %s\n", incoming_dir);
        exit(1);
    }
    if (setuid(uid) < 0) {
        perror("setuid");
        exit(1);
    }

    statvfs(incoming_dir, &st);
    // take a little margin with 90% of the free space
    root_free_space = max(0, st.f_bfree * st.f_bsize * 0.90);

    bytes_limit = min(root_free_space, DEFAULT_MAX_UPDATES_BYTES);

    if ((var=getenv("UPDATES_MAX_BYTES")))
        bytes_limit = atoll(var);
    if ((var=getenv("UPDATES_MAX_FILES")))
        files_limit = atoll(var);

    set_size_limit(bytes_limit, files_limit);
    for (i = 3; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0) {
            set_verbose(1);
        } else if (strcmp(argv[i], "-w") == 0) {
            if (i+1 < argc && argv[i+1][0] != '-') {
                unsigned long space_margin;

                errno = 0;
                space_margin = strtoul(argv[i+1], &var, 0);
                if (*argv[i+1] < '0' || *argv[i+1] > '9' || *var != '\0' ||
                        (space_margin == ULONG_MAX && errno == ERANGE)) {
                    fprintf(stderr, "Invalid value for -w option: %s\n", argv[i+1]);
                    exit(1);
                }
                set_wait_for_space(space_margin);
                i++;
            } else {
                set_wait_for_space(1);
            }
        } else {
            fprintf(stderr, "Invalid option %s", argv[i]);
            exit(1);
        }
    }
    return do_unpack();
}
