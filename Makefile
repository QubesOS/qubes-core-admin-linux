
PYTHON ?= python3

clean:
	@true

all:
	$(PYTHON) setup.py build

install:
	$(PYTHON) setup.py install -O1 --root=$(DESTDIR) --skip-build --record=INSTALLED_FILES
	export INSTALLED_FILES