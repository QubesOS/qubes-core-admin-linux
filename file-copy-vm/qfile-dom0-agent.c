#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <libgen.h>
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

int main(int argc, char **argv)
{
    int i;
    int ignore_symlinks = 0;
    int invocation_cwd_fd;
    char *arg_dirname_in;
    char *arg_dirname;
    char *arg_basename_in;
    char *arg_basename;

    qfile_pack_init();
    register_error_handler(display_error);
    invocation_cwd_fd = open(".", O_PATH | O_DIRECTORY);
    if (invocation_cwd_fd < 0)
        gui_fatal("open \".\"");
    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--ignore-symlinks")==0) {
            ignore_symlinks = 1;
            continue;
        }
        if (!*argv[i])
            gui_fatal("Invalid empty argument %i", i);

        arg_dirname_in = strdup(argv[i]);
        if (!arg_dirname_in)
            gui_fatal("strdup for dirname of %s", argv[i]);
        arg_dirname = dirname(arg_dirname_in);

        arg_basename_in = strdup(argv[i]);
        if (!arg_basename_in)
            gui_fatal("strdup for basename of %s", argv[i]);
        arg_basename = basename(arg_basename_in);

        if (fchdir(invocation_cwd_fd))
            gui_fatal("fchdir to %i", invocation_cwd_fd);
        if (chdir(arg_dirname))
            gui_fatal("chdir to %s", arg_dirname);
        do_fs_walk(arg_basename, ignore_symlinks);

        free(arg_dirname_in);
        free(arg_basename_in);
    }
    notify_end_and_wait_for_result();
    return 0;
}
