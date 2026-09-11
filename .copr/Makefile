# COPR's "Make" source method runs `make -f .copr/Makefile srpm outdir=<dir>`
# in a minimal chroot after cloning this repository, and builds whatever SRPM
# the target leaves in $(outdir). A webhook on this repo is what turns a push
# into a build; see docs/packaging.md.
#
# The version comes from codinian/version.py rather than from the spec, so that
# a mismatch between the two shows up here as a missing tarball rather than as a
# package quietly built from the wrong source. packaging/check-versions.sh is
# what keeps them in step.

outdir ?= $(CURDIR)/build
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"$$/\1/p' codinian/version.py)

.PHONY: srpm
srpm:
	command -v rpmbuild >/dev/null || dnf -y install rpm-build
	mkdir -p $(outdir) $(CURDIR)/.copr-sources
	git archive --format=tar.gz --prefix=codinian-$(VERSION)/ \
		-o $(CURDIR)/.copr-sources/codinian-$(VERSION).tar.gz HEAD
	rpmbuild -bs \
		--define "_sourcedir $(CURDIR)/.copr-sources" \
		--define "_srcrpmdir $(outdir)" \
		packaging/rpm/codinian.spec
