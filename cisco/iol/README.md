# Cisco IOL (IOS on Linux)

This is the containerlab/vrnetlab image for Cisco IOL (IOS On Linux). You can find the binary image as part of the CML image, with this [blog](https://marcstech.blog/archives/add-cisco-iol-containerlab-macos/) explaining the process.

Compared to other Cisco images, IOL runs very lightly: it executes purely as a binary and needs no virtualisation layer.

There are two types of IOL you can obtain:

- IOL, meant for Layer 3 operation as a router.
- IOL-L2, meant to act as a L2/L2+ switch.

## Building the image

Copy your IOL image into this directory, named as below, then run `make` or `make docker-image`.

| From                       | File                                  | Name it as                  | Tag           |
| -------------------------- | ------------------------------------- | --------------------------- | ------------- |
| CML 2.10+ (IOL)            | `iol-xe-17-18-02.tar.gz`              | keep as-is                  | `17.18.02`    |
| CML 2.10+ (IOL-L2)         | `ioll2-xe-17-18-02.tar.gz`            | keep as-is                  | `L2-17.18.02` |
| CML 2.9 and older (IOL)    | `x86_64_crb_linux-adventerprisek9-ms` | `cisco_iol-17.12.01.bin`    | `17.12.01`    |
| CML 2.9 and older (IOL-L2) | `x86_64_crb_linux-adventerprisek9-ms` | `cisco_iol-L2-17.12.01.bin` | `L2-17.12.01` |

From CML 2.10 the refplat ships IOL as a container image archive; the build pulls the IOL binary
out of it for you. Older refplats ship a loose binary under `iol-xe-x.y.z/` (or `ioll2-xe-x.y.z/`),
which you must rename yourself — the `.bin` extension is what the build looks for.

Check the result with `docker images`:

```sh
REPOSITORY            TAG           IMAGE ID       CREATED          SIZE
vrnetlab/cisco_iol    L2-17.12.01   c207d920446e   5 seconds ago    607MB
vrnetlab/cisco_iol    17.12.01      30be6c875c80   12 minutes ago   704MB
```

### Extracting the binary by hand

Only needed if the automatic extraction breaks. The CML 2.10 archive is a container image archive;
the IOL binary sits inside one of its layers.

```sh
# list the layers, in order
tar -xzOf iol-xe-17-18-02.tar.gz manifest.json

# list a layer's contents; the binary is a regular file ending in .iol
# (binary.iol is only a symlink to it)
tar -xzOf iol-xe-17-18-02.tar.gz blobs/sha256/<layer> | tar -tvf -

# pull it out, without unpacking the rest of the archive
tar -xzOf iol-xe-17-18-02.tar.gz blobs/sha256/<layer> \
  | tar -xOf - x86_64_crb_linux-adventerprisek9-ms.iol > cisco_iol-17.18.02.bin
```

## Usage

Define the image in a topology. For IOL-L2, add `type: l2`.

```yaml
# topology.clab.yaml
name: mylab
topology:
  nodes:
    iol:
      kind: cisco_iol
      image: vrnetlab/cisco_iol:<tag>
      # type: l2  # only for IOL-L2
```
