# vrnetlab - VR Network Lab

This is a fork of the original [plajjan/vrnetlab](https://github.com/plajjan/vrnetlab)
project and was created specifically to make vrnetlab-based images runnable by
[containerlab](https://containerlab.srlinux.dev).

The documentation provided in this fork only explains the parts that have been
changed from the upstream project. To get a general overview of the vrnetlab
project itself, consider reading the [docs of the upstream repo](https://github.com/vrnetlab/vrnetlab/blob/master/README.md).

## What is this fork about?

At [containerlab](https://containerlab.srlinux.dev) we needed to have
[a way to run virtual routers](https://containerlab.dev/manual/vrnetlab/)
alongside the containerized Network Operating Systems.

Vrnetlab provides perfect machinery to package most-common routing VMs in
container packaging. What upstream vrnetlab doesn't do, though, is create
datapaths between the VMs in a "container-native" way.

Vrnetlab relies on a separate VM ([vr-xcon](https://github.com/vrnetlab/vrnetlab/tree/master/vr-xcon))
to stitch sockets exposed on each container and that doesn't play well with the
regular ways of interconnecting container workloads.

This fork adds the additional option `connection-mode` to the `launch.py` script
of supported VMs. The `connection-mode` option controls how vrnetlab creates
datapaths for launched VMs.

The `connection-mode` values make it possible to run vrnetlab containers with
networking that doesn't require a separate container and is native to tools such
as docker.

### Container-native networking?

Yes, the term is bloated. What it actually means is this fork makes it possible
to add interfaces to a container hosting a qemu VM and vrnetlab will recognize
those interfaces and stitch them with the VM interfaces.

With this you can, for example, add veth pairs between containers as you would
normally and vrnetlab will make sure these ports get mapped to your routers'
ports. In essence, that allows you to work with your vrnetlab containers like a
normal container and get the datapath working in the same "native" way.

> [!IMPORTANT]
> Although the changes we made here are of a general purpose and you can run
> vrnetlab routers with docker CLI or any other container runtime, the purpose
> of this work was to couple vrnetlab with containerlab.
>
> With this being said, we recommend the readers start their journey from
> this [documentation entry](https://containerlab.dev/manual/vrnetlab/)
> which will show you how easy it is to run routers in a containerized setting.

## Connection modes

As mentioned above, the major change this fork brings is the ability to run
vrnetlab containers without requiring [vr-xcon](https://github.com/vrnetlab/vrnetlab/tree/master/vr-xcon)
and instead uses container-native networking.

For containerlab the default connection mode value is `connection-mode=tc`.
With this particular mode we use **tc-mirred** redirects to stitch a container's
interfaces `eth1+` with the ports of the qemu VM running inside.

![diagram showing network connections via tc redirects](https://gitlab.com/rdodin/pics/-/wikis/uploads/4d31c06e6258e70edc887b17e0e758e0/image.png)

Using tc redirection (tc-mirred) we get a transparent pipe between a container's
interfaces and those of the VMs running within.

We scrambled through many connection alternatives, which are described in
[this post](https://netdevops.me/2021/transparently-redirecting-packetsframes-between-interfaces/),
but tc redirect (tc-mirred :star:) works best of all.

### Mode List

Full list of connection mode values:

| Connection Mode | LACP Support        | Description |
| --------------- | :-----------------: | :---------- |
| tc-mirred       | :white_check_mark:  | Creates a linux bridge and attaches `eth` and `tap` interfaces to it. Cleanest solution for point-to-point links.
| bridge          | :last_quarter_moon: | No additional kernel modules and has native qemu/libvirt support. Does not support passing STP. Requires restricting `MAC_PAUSE` frames in order to support LACP.
| ovs-bridge      | :white_check_mark:  | Same as a regular bridge, but uses OvS (Open vSwitch).
| macvtap         | :x:                 | Requires mounting entire `/dev` to a container namespace. Needs file descriptor manipulation due to no native qemu support.

## Management interface

There are two types of management connectivity for NOS VMs: _pass-through_ and _host-forwarded_ (legacy) management interfaces.

_Pass-through management_ interfaces allows the use of the assigned management IP within the NOS VM, management traffic is transparently passed through to the VM, and the NOS configuration can accurately reflect the management IP. However, it is no longer possible to send or receive traffic directly in the vrnetlab container (e.g. for installing additional packages within the container), other than to pre-defined exceptions, such as the QEMU serial port on TCP port 5000.

NOSes defaulting to _pass-through_ management interfaces are:

* None so far, we are gathering feedback on this, and will update this list as feedback is received. Please contact us in [Discord](https://discord.gg/vAyddtaEV9) or open up an issue here if you have found any issues when trying the passthrough mode.

In case of _host-forwarded_ management interfaces, certain ports are forwarded to the NOS VM IP, which is always 10.0.0.15/24. The management gateway in this case is 10.0.0.2/24, and outgoing traffic is NATed to the container management IP. This management interface connection mode does not allow for traffic such as LLDP to pass through the management interface.

NOSes defaulting to _host-forwarded_ management interfaces are:

* all current systems

It is possible to change from the default management interface mode by setting the `CLAB_MGMT_PASSTHROUGH` environment variable to 'true' or 'false', however, it is left up to the user to provide a startup configuration compatible with the requested mode.

## Resetting Virtual Routers

You can force-reset all or a subset of VR component VMs by creating a file named `reset` in the root path (`/`) of the container. This triggers a qemu-monitor `system_reset` command to the specified VM(s), which acts like a hardware reset button without disrupting existing qemu VM properties (interfaces, disks...). Useful for recovering VMs in a hung state or for testing scenarios.

 - To reset the VR (all component VMs), create an empty `/reset` file.
 - To reset a subset of the VR VMs, write a comma-separated list of VM numbers to the `/reset` file.

Usage examples:
```bash
sudo docker exec container_name_or_id touch /reset #reset VR (all component VMs)
sudo docker exec container_name_or_id sh -c 'echo "0" > /reset' #reset VM 0
sudo docker exec container_name_or_id sh -c 'echo "1,2" > /reset' #reset VM 1 & VM 2
```

> **Note:**  
> VM numbers correspond to the internal numbering used by vrnetlab (typically starting from 0).

## Snapshotting virtual routers

You can snapshot all VMs in a running container by adding a `/snapshot-save` file to the root path of the container.
This prompts the container to pause the VM and save VM states, disk overlays and metadata to `/snapshot-output.tar` before resuming the VM.
Using this snapshot file you can start nodes using the same image up to the exact same state the VM had when you took the snapshot.

This can save a lot of time for workflows that rely on VMs reaching a known-good state.

**Basic usage**
```bash
# Start a VM and wait for it to boot
docker run -d --name node --privileged vrnetlab/{your-image}

# Create snapshot
docker exec node touch /snapshot-save

# Extract snapshot tar
docker cp node:/snapshot-output.tar ./snapshot.tar

# Start new container with snapshot
docker run -d --name restored-node --privileged \ 
  -e RESTORE_SNAPSHOT=1 \ 
  -v $(pwd)/snapshot.tar:/snapshot.tar:ro \ 
  vrnetlab/{your-image}
```

## Which vrnetlab routers are supported?

Since the changes we made in this fork are VM specific, we added a few popular
routing products:

* Arista vEOS
* Cisco XRv9k
* Cisco XRv
* Cisco FTDv
* Juniper vMX
* Juniper vSRX
* Juniper vJunos-switch
* Juniper vJunos-router
* Juniper vJunosEvolved
* Nokia SR OS
* OpenBSD
* FreeBSD
* Ubuntu

The rest are left untouched and can be contributed back by the community.

## Does the build process change?

No. You build the images exactly as before.
