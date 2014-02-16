CC=gcc
CFLAGS=-g -I. -Wall -Wextra -Werror -fPIC -pie
qfile-dom0-unpacker: qfile-dom0-unpacker.o
	$(CC) -pie -g -o $@ $^ -lqubes-rpc-filecopy
