CFLAGS=-g -O2 -Wall -Wextra -Werror -fPIC -pie
qubes-pci-quarantine: qubes-pci-quarantine.o
	$(CC) -pie -g -o $@ $^ -lxenctrl

clean:
	rm -f qubes-pci-quarantine *.o
