# Fortinet FortiGate

Fortinet FortiGate/FortiOS support for vrnetlab and Containerlab.

The launcher supports recent FortiGate VM images, including FortiOS 8.0, and
similar Fortinet CLI families such as FortiProxy where the boot and prompt
patterns are compatible.

## Build

Place one FortiOS `qcow2` image in this directory. The Makefile expects the
image name to use this format:

```text
fortios-vX.Y.Z.qcow2
```

Build the image:

```bash
make docker-build-fortigate
```

Run the image manually:

```bash
make docker-run-fortigate
```

## Containerlab

Use `kind: fortinet_fortigate`.

```yaml
name: fgt-lab

topology:
  nodes:
    fgt:
      kind: fortinet_fortigate
      image: vr-fortios:8.0.0
      enforce-startup-config: true
      startup-config: configs/fgt.conf
      license: licenses/appliance.lic
      credentials:
        username: admin
        password: admin
      env:
        CLAB_MGMT_PASSTHROUGH: "true"
        FOS_DISK_SPECS: "10g,10g"
        FOS_LICENSE_STATUS_TIMEOUT_SECONDS: "120"
        FOS_LOG_LEVEL: "DEBUG"
        FOS_MGMT_DNS_PRIMARY: "1.1.1.1"
        FOS_MGMT_DNS_SECONDARY: "8.8.8.8"
        FOS_NO_ENC_CONFIG: "false"
        FOS_ONBOARDING: "false"
        FOS_UUID: "6c6323d5-0713-58eb-9458-4f8803a2cd93"
```

### Node Options

`startup-config` mounts a FortiOS config file that the launcher applies after
bootstrap, hostname setup, license handling, admin setup, and baseline config
capture. The file is available inside the container as
`/config/startup-config.cfg`.

`license` mounts a FortiGate VM license. The launcher expects it inside the
container as `/tftpboot/appliance.lic`, installs it with
`execute restore vmlicense tftp`, handles the reboot, and waits for license
status to leave `Pending`.

`credentials` sets the desired final administrator account. If omitted, the
final account is `admin` / `admin`. The bootstrap flow handles first-login
password change prompts and FortiOS versions that initially accept a blank
default password.

`enforce-startup-config: true` is recommended so Containerlab always mounts and
applies the intended startup config.

## Environment Variables

| Variable | Default | Values | Description |
| --- | --- | --- | --- |
| `CLAB_MGMT_PASSTHROUGH` | `true` | `true`, `false` | Selects management wiring. `true` uses tap/tc passthrough so the FortiGate management interface participates directly in the Containerlab management network. `false` uses a host-forwarded bridge inside the vrnetlab container. |
| `FOS_DISK_SPECS` | unset | comma-separated `qemu-img create` sizes, for example `10g` or `10g,10g` | Adds extra virtio disks. One disk becomes the FortiGate log disk. Additional disks are formatted during bootstrap; the second disk is expected to become WAN optimization storage on FortiOS versions that support it. |
| `FOS_DEBUG_FEATURE` | unset | feature name | Runs bootstrap only through the named feature, then skips later features. Public feature order: `disk-format`, `admin`, `management`, `bootstrap-dns`, `setup-license`, `default-config`, `management-after-license`, `license-validation`, `management-vrf`, `undo-bootstrap-dns`, `fortitoken-provisioning`, `capture-config`, `startup-config`. |
| `FOS_LICENSE_STATUS_TIMEOUT_SECONDS` | `120` | seconds | Maximum time to poll `get system status` for license status to leave `Pending` after license installation. |
| `FOS_LOG_LEVEL` | `DEBUG` | `TRACE`, `DEBUG`, `INFO`, `WARN`, `ERROR`, or numeric Python log level | Sets launcher log verbosity. |
| `FOS_MGMT_DNS_PRIMARY` | `1.1.1.1` | IPv4 address | Sets the primary DNS server used temporarily during bootstrap. The launcher unsets it before baseline capture and startup config application. |
| `FOS_MGMT_DNS_SECONDARY` | `8.8.8.8` | IPv4 address | Sets the secondary DNS server used temporarily during bootstrap. The launcher unsets it before baseline capture and startup config application. The legacy misspelling `FOS_MGMG_DNS_SECONDARY` remains accepted. |
| `FOS_NO_ENC_CONFIG` | `false` | `true`, `false` | When `true`, ignores ENC-only changes on entries that already exist in the baseline. New entries and entries with other changes retain their encrypted fields. |
| `FOS_ONBOARDING` | `false` | `true`, `false` | When `true`, disables the HTTPS redirect and automatic-upgrade setup warning in the default FortiOS GUI configuration. |
| `FOS_UUID` | random UUID | UUID string | Sets the QEMU VM UUID. If unset, a new UUID is generated for each launch. |

Containerlab also passes the usual vrnetlab launch arguments such as hostname,
username, password, and connection mode. For manual runs these are available as
launcher arguments:

```text
--hostname
--username
--password
--connection-mode
```

## Management Modes

### Passthrough Management

`CLAB_MGMT_PASSTHROUGH=true` is the default. The launcher creates a tap device
for `port1` and uses tc rules to redirect management traffic between the
FortiGate VM and the container management interface. TCP serial ports
`5000-5007` are passed through to the container instead of being redirected to
the VM management interface.

The TFTP server used for license installation runs in a dedicated namespace and
is reachable from the FortiGate through the management gateway address.

### Host-Forwarded Management

`CLAB_MGMT_PASSTHROUGH=false` creates an internal `br-mgmt` bridge and configures
FortiGate `port1` with:

```text
172.31.255.30/30 via 172.31.255.29
200::1/127 via 200::
```

TCP traffic that enters the container, except the serial console on port `5000`,
is DNATed to the FortiGate management address. UDP traffic is also DNATed so
license TFTP can work.

If FortiOS later receives DHCP on the management interface, disable the
FortiGate default gateway only after adding a route back to the management
subnet. Disabling it first can cut off management access.

## Startup Config

The startup config is applied line by line after the launcher has finished its
own bootstrap commands. Keep it as ordinary FortiOS CLI config:

```text
config system global
    set alias "lab-fgt"
end
```

The importer validates basic `config` / `edit` / `next` / `end` nesting and
fails startup on malformed structure.

## Default Startup Configuration

Before the user startup config is applied, the launcher leaves this baseline in
the node when management IPv4 is static. Values in angle brackets are derived
from Containerlab management settings and node credentials. With DHCP
management, the static `port1` and route `9999` configuration is omitted.

```text
config system interface
    edit port1
        set mode static
        set ip <management-ipv4/prefix>
        set allowaccess ping https ssh http
        config ipv6                         # when management IPv6 is configured
            set ip6-mode static
            set ip6-address <management-ipv6/prefix>
            set ip6-allowaccess ping https ssh http
        end
    next
end

config system fortiguard
    set interface-select-method specify
    set interface port1
    set auto-join-forticloud disable
end

config router static
    edit 9999
        set gateway <management-ipv4-gateway>
        set device port1
    next
end

config router static6                       # when management IPv6 is configured
    edit 9999
        set gateway <management-ipv6-gateway>
        set device port1
    next
end

config system global
    set admin-scp enable
    set admin-https-redirect disable             # when FOS_ONBOARDING=true
    set gui-auto-upgrade-setup-warning disable   # when FOS_ONBOARDING=true
    set hostname <node-name>
end

config system password-policy
    set status disable
end

config system admin
    edit <username>                          # `admin` by default
        set accprofile super_admin
        set password <password>              # `admin` by default
    next
end

config system console
    set output more
end
```

The DNS servers selected by `FOS_MGMT_DNS_PRIMARY` and
`FOS_MGMT_DNS_SECONDARY` are configured only while the launcher is bootstrapping
and are then unset. They are not part of the final baseline.

When a license is installed, the launcher reapplies management configuration
after the license reboot and attempts to place `port1` and route `9999` in VRF
1. On FortiProxy, where `set vrf 1` is unsupported, route `9999` is narrowed to
the management gateway destination using the management address prefix; this
avoids overriding a lab default route.

The launcher captures this baseline before it imports the user startup config.
The startup config can override preceding baseline commands, except that the
launcher restores console pagination to `more` after import.

## Licensing

When `/tftpboot/appliance.lic` exists, the launcher installs it during startup.
License installation may reboot the VM and may remove the active admin session
when the status changes to `VALID`; the launcher handles re-login and continues
bootstrap.

After installation, the launcher polls `get system status` until the license
field is no longer `Pending`. By default it waits up to 2 minutes. Set
`FOS_LICENSE_STATUS_TIMEOUT_SECONDS` to override that timeout for shorter
targeted runs.

## Extra Disks

Set `FOS_DISK_SPECS` to add disks:

```yaml
env:
  FOS_DISK_SPECS: "10g,10g"
```

This creates `empty1.qcow2`, `empty2.qcow2`, and so on, and attaches them as
virtio drives. FortiOS normally formats the first additional disk as log
storage. The launcher formats remaining configured disks during bootstrap.

Expected FortiOS storage usage for common test cases:

```text
FOS_DISK_SPECS unset      -> no configured storage usage
FOS_DISK_SPECS="10g"     -> order 1 usage log
FOS_DISK_SPECS="10g,10g" -> order 1 usage log, order 2 usage wanopt
```

## Saving Config

Touch `/get-config` inside a running container to ask the launcher to capture the
current FortiOS config:

```bash
docker exec clab-<lab>-<node> touch /get-config
```

The launcher consumes the trigger file when it detects it. Creating the file
requests a capture; later modification or deletion of that file does not.

The launcher reconnects to the serial console, runs `show`, compares the result
with the baseline captured before startup config application, and writes the
changed config to:

```text
/config/current.conf
```

The serial connection is closed after capture so the console remains available
for external use. Capture requests standard console output from the launcher;
when the VM originally used pagination, it is restored after capture and before
the user startup configuration is applied.

The comparison parses `config` and `edit` blocks into a tree. Unchanged entries
are skipped. New entries are written completely, including encrypted fields.
For an existing entry, any non-encrypted body change causes its complete current
subtree to be written, including encrypted fields and any nested `config`
blocks. Parent `config` / `edit` ancestry is retained so `current.conf` remains
replayable.

FortiOS may emit a different `ENC` representation on each `show`. By default,
an ENC-only difference therefore saves the complete existing entry. Set
`FOS_NO_ENC_CONFIG=true` to ignore ENC-only differences; encrypted fields are
still retained when a new entry or another body change causes that entry to be
saved.

## Boot Features

The FortiOS launcher uses a CLI finite-state machine rather than fixed sleeps.
User-visible behavior includes:

- detection of login, password, forced password-change, rejected credentials,
  welcome banner, reboot, shutdown, and command prompts
- buffered prompt matching for fragmented serial output
- hostname update and prompt-pattern update during bootstrap
- `admin-scp` enabled for configuration backup support
- default credential handling across FortiOS 6.4, 7.x, and 8.x behavior
- password-policy failure surfaced as a startup error
- explicit failure if no `qcow2` image is present
- full serial output logging at debug level

## Tested Versions

Tested with:

- FortiGate 8.0.0 build 0167 GA debug image
- FortiGate 7.6.6 build 3652 GA debug image
- FortiGate 7.4.12 build 2902 GA
- FortiGate 7.0.19 build 0696 GA
- FortiGate 6.4.16 build 2098 GA
- FortiProxy 7.6.6 build 1628 GA
- FortiProxy 7.4.13 build 0722 GA debug image
- FortiProxy 7.2.16 build 0465 GA
- FortiProxy 7.0.23 build 0222 GA
