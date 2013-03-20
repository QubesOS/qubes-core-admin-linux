CC=gcc
CFLAGS=-g -I. -Wall -fPIC -pie
qfile-dom0-unpacker: qfile-dom0-unpacker.o
	$(CC) -pie -g -o $@ $^ -lqubes-rpc-filecopy
