#!/bin/bash

version="9"

# Download latest rocky cloud image
download_url="https://ftp.lysator.liu.se/pub/rocky/$version/images/x86_64/Rocky-$version-GenericCloud-Base.latest.x86_64.qcow2"

# Extract the filename from the URL
filename="$version-rocky-cloud.qcow2"

# Check if the file already exists in the current directory
if [ -e "$filename" ]; then
    echo "File $filename already exists. Skipping download."
else
    # Download the URL
    curl -o $filename "$download_url"
    echo "Download complete: $filename"
fi
