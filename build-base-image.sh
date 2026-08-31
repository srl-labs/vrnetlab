#!/usr/bin/env bash
# this script builds the vrnetlab base container image
# that is used in the dockerfiles of the NOS images

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <version>"
    exit 1
fi

sudo docker buildx build -t ghcr.io/srl-labs/vrnetlab-base:$1 \
    -f vrnetlab-base.dockerfile .