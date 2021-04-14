RPMS_DIR=rpm/

VERSION := $(shell cat version)
VERSION_VAIO_FIXES := $(shell cat version_vaio_fixes)

DIST_DOM0 ?= fc32

help:
	@echo "make rpms                  -- generate binary rpm packages"
	@echo "make rpms-dom0             -- generate binary rpm packages for Dom0"
	@echo "make update-repo-current   -- copy newly generated rpms to qubes yum repo"
	@echo "make update-repo-current-testing  -- same, but to -current-testing repo"
	@echo "make update-repo-unstable  -- same, but to -testing repo"
	@echo "make update-repo-installer -- copy dom0 rpms to installer repo"
	@echo "make clean                 -- cleanup"

rpms: rpms-dom0

rpms-vm:
	@true

rpms-dom0: rpms-vaio-fixes
	rpmbuild --define "_rpmdir $(RPMS_DIR)" -bb rpm_spec/core-dom0-linux.spec
	rpm --addsign \
		$(RPMS_DIR)/x86_64/qubes-core-dom0-linux-$(VERSION)*.rpm

rpms-vaio-fixes:
	rpmbuild --define "_rpmdir $(RPMS_DIR)" -bb rpm_spec/core-dom0-vaio-fixes.spec
	rpm --addsign $(RPMS_DIR)/x86_64/qubes-core-dom0-vaio-fixes-$(VERSION_VAIO_FIXES)*.rpm

clean:
	@true
