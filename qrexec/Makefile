CC=gcc
CFLAGS+=-I. -g -Wall -Wextra -Werror -pie -fPIC
XENLIBS=-lvchan -lxenstore -lxenctrl
LIBS=$(XENLIBS) -lqrexec-utils

all: qrexec-daemon qrexec-client 
qrexec-daemon: qrexec-daemon.o
	$(CC) -pie -g -o qrexec-daemon qrexec-daemon.o $(LIBS)
qrexec-client: qrexec-client.o
	$(CC) -pie -g -o qrexec-client qrexec-client.o $(LIBS)
clean:
	rm -f *.o *~ qrexec-daemon qrexec-client
