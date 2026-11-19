#!/bin/bash

version="v1.10.5"

# Download latest jammy lts cloud image
download_url="https://factory.talos.dev/image/9ddb0c3e6bf64299a4013243fd14a209af1d0626ebf8e1eb4a151f897cd8f8f2/${version}/metal-amd64.qcow2"

# Extract the filename from the URL
filename="$version-metal-amd64.qcow2"

# Check if the file already exists in the current directory
if [ -e "$filename" ]; then
  echo "File $filename already exists. Skipping download."
else
  # Download the URL
  curl -o $filename "$download_url"
  echo "Download complete: $filename"
fi
