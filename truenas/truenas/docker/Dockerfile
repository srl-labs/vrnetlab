FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

ARG VERSION
ENV VERSION=${VERSION}

# empty boot disk: `make` boots the TrueNAS ISO against it (launch.py --install) and commits the result into the image
RUN qemu-img create -f qcow2 /truenas.qcow2 16G

COPY *.py /

EXPOSE 22 80 443 5000 10000-10099
