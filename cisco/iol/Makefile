VENDOR=Cisco
NAME=IOL
IMAGE_FORMAT=bin
IMAGE_GLOB=*.bin *.tar.gz
DONT_COPY_COMMON=1

# match versions like:
# cisco_iol-17.12.01.bin      -> 17.12.01      loose binary (CML <= 2.9 refplat)
# cisco_iol-L2-17.12.01.bin   -> L2-17.12.01
# iol-xe-17-18-02.tar.gz      -> 17.18.02      container archive (CML >= 2.10)
# ioll2-xe-17-18-02.tar.gz    -> L2-17.18.02
VERSION=$(shell case '$(IMAGE)' in \
	(ioll2-xe-*.tar.gz) echo '$(IMAGE)' | sed -e 's/^ioll2-xe-//' -e 's/\.tar\.gz$$//' -e 's/-/./g' -e 's/^/L2-/' ;; \
	(iol-xe-*.tar.gz) echo '$(IMAGE)' | sed -e 's/^iol-xe-//' -e 's/\.tar\.gz$$//' -e 's/-/./g' ;; \
	(*) echo '$(IMAGE)' | sed -e 's/cisco_iol-\(.*\)\.bin/\1/' ;; \
	esac)

-include ../../makefile-sanity.include
-include ../../makefile.include

# From CML 2.10 onwards IOL is shipped as an OCI/docker image archive instead of
# a loose binary. The IOL ELF sits inside one of the layer blobs, so peel it out
# on the fly rather than making the user hunt for it by hand.
#
# The archive is two tars deep:
#
#   iol-xe-17-18-02.tar.gz
#   |-- manifest.json         names the config + layer blobs, layers bottom-first
#   `-- blobs/sha256/
#       |-- 483954...         config JSON
#       |-- 9f410b...         layer 0, itself a tar: libc, iol-runner, .iourc
#       `-- ac6972...         layer 1, itself a tar: ...plus the .iol binary
#
# So: read manifest.json for the blob paths, then probe each blob (stream it out
# of the outer tar and list it with an inner tar) for a member ending in .iol.

docker-build-image-copy:
	@case '$(IMAGE)' in \
	*.tar.gz) \
		echo "--> $(IMAGE) is a container archive, extracting IOL binary"; \
		blob=; member=; \
		for b in $$(tar -xzOf '$(IMAGE)' manifest.json | grep -o 'blobs/sha256/[0-9a-f]\{64\}'); do \
			m=$$(tar -xzOf '$(IMAGE)' "$$b" 2>/dev/null | tar -tvf - 2>/dev/null \
				| awk '/^-.*\.iol$$/ { print $$NF }' | tail -1); \
			if [ -n "$$m" ]; then blob=$$b; member=$$m; fi; \
		done; \
		if [ -z "$$member" ]; then \
			echo "ERROR: no IOL binary (*.iol) found in any layer of $(IMAGE)"; exit 1; \
		fi; \
		echo "--> Found $$member in $$blob"; \
		tar -xzOf '$(IMAGE)' "$$blob" | tar -xOf - "$$member" > docker/iol.bin; \
		[ -s docker/iol.bin ] || { echo "ERROR: extracted IOL binary is empty"; exit 1; }; \
		;; \
	*) cp $(IMAGE)* docker/ ;; \
	esac
