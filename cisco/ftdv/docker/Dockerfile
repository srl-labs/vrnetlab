FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0


ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 80 161/udp 443 5000 8305 10000-10099
