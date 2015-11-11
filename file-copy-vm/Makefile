CFLAGS=-g -O2 -Wall -Wextra -Werror -I. -fPIC -pie
all:    qfile-dom0-agent 
qfile-dom0-agent: qfile-dom0-agent.o
	$(CC) -pie -g -o $@ $^ -lqubes-rpc-filecopy

clean:
	rm -f qfile-dom0-agent *.o

