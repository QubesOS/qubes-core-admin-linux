#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <signal.h>
#include <fcntl.h>
#include <malloc.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <libqubes-rpc-filecopy.h>

void display_error(const char *fmt, va_list args) {
    char *dialog_cmd;
    char buf[1024];
    struct stat st_buf;
    int ret;

    (void) vsnprintf(buf, sizeof(buf), fmt, args);
    ret = stat("/usr/bin/kdialog", &st_buf);
#define KDIALOG_CMD "kdialog --title 'File copy/move error' --sorry "
#define ZENITY_CMD "zenity --title 'File copy/move error' --warning --text "
    if (asprintf(&dialog_cmd, "%s '%s: %s (error type: %s)'",
                ret==0 ? KDIALOG_CMD : ZENITY_CMD,
                program_invocation_short_name, buf, strerror(errno)) < 0) {
        fprintf(stderr, "Failed to allocate memory for error message :(\n");
        return;
    }
#undef KDIALOG_CMD
#undef ZENITY_CMD
    fprintf(stderr, "%s\n", buf);
    system(dialog_cmd);
}

_Noreturn void gui_fatal(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    display_error(fmt, args);
    va_end(args);
    exit(1);
}

char *get_abs_path(const char *cwd, const char *pathname)
{
    char *ret;
    if (pathname[0] == '/')
        return strdup(pathname);
    if (asprintf(&ret, "%s/%s", cwd, pathname) < 0)
        return NULL;
    else
        return ret;
}

int main(int argc, char **argv)
{
    int i;
    char *entry;
    char *cwd;
    char *sep;
    int ignore_symlinks = 0;

    qfile_pack_init();
    register_error_handler(display_error);
    cwd = getcwd(NULL, 0);
    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--ignore-symlinks")==0) {
            ignore_symlinks = 1;
            continue;
        }

        entry = get_abs_path(cwd, argv[i]);

        do {
            sep = rindex(entry, '/');
            if (!sep)
                gui_fatal
                    ("Internal error: nonabsolute filenames not allowed");
            *sep = 0;
        } while (sep[1] == 0);
        if (entry[0] == 0) {
            if (chdir("/") < 0) {
                gui_fatal("Internal error: chdir(\"/\") failed?!");
            }
        } else if (chdir(entry))
            gui_fatal("chdir to %s", entry);
        do_fs_walk(sep + 1, ignore_symlinks);
        free(entry);
    }
    notify_end_and_wait_for_result();
    return 0;
}


