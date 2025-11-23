#!/bin/bash

filename=vpp-proto-bookworm.qcow2
# Check if the file already exists in the current directory
if [ -e "$filename" ]; then
    echo "File $filename already exists. Skipping download."
else
    wget https://ipng.ch/media/vpp-proto/vpp-proto-bookworm.qcow2.lrz
    apt install lrzip
    lrzip -d vpp-proto-bookworm.qcow2.lrz
fi
