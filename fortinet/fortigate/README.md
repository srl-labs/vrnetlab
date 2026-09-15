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
| `FOS_DEBUG_FEATURE` | unset | feature name | Runs bootstrap only through the named feature, then skips later features. Public feature order: `disk-format`, `admin`, `system-version`, `management`, `bootstrap-dns`, `fortiguard-hooks`, `setup-license`, `fortiguard-hooks-after-license`, `default-config`, `management-after-license`, `license-validation`, `fortitoken-provisioning`, `pki-certificates`, `management-vrf`, `capture-config`, `startup-config`. |
| `FOS_EXIT_ON_BOOTSTRAP_ERROR` | `true` | `true`, `false` | Stops the launcher with a nonzero exit status when a bootstrap feature raises an error. Set to `false` only for manual recovery or diagnostics; the launcher logs the first error, leaves the VM running, and halts bootstrap without reporting startup complete. |
| `FOS_HEALTHCHECK_STALL_SECONDS` | `90` | seconds | How long the Docker healthcheck probe waits for a launcher heartbeat before reporting failure. Only relevant for diagnostics; the launcher must stay responsive within this window. See [Container Healthcheck](#container-healthcheck). |
| `FOS_HEARTBEAT_FILE` | `/healthbeat` | container path | File the launcher appends one byte to on every main-loop iteration; the healthcheck probe watches it to distinguish a slow bootstrap from a wedged launcher. |
| `FOS_LICENSE_STATUS_TIMEOUT_SECONDS` | `120` | seconds | Maximum time to poll `get system status` for license status to leave `Pending` after license installation. |
| `FOS_LOG_LEVEL` | `DEBUG` | `TRACE`, `DEBUG`, `INFO`, `WARN`, `ERROR`, or numeric Python log level | Sets launcher log verbosity. |
| `FOS_MGMT_DNS_PRIMARY` | `1.1.1.1` | IPv4 address | Sets the primary DNS server configured during bootstrap for license and FortiGuard reachability. The setting persists after bootstrap; the launcher does not remove it. |
| `FOS_MGMT_DNS_SECONDARY` | `8.8.8.8` | IPv4 address | Sets the secondary DNS server configured during bootstrap. Like the primary, it persists after bootstrap. The legacy misspelling `FOS_MGMG_DNS_SECONDARY` remains accepted. |
| `FOS_NO_ENC_CONFIG` | `false` | `true`, `false` | When `true`, ignores ENC-only changes on entries that already exist in the baseline. New entries and entries with other changes retain their encrypted fields. |
| `FOS_ONBOARDING` | `false` | `true`, `false` | When `true`, disables the HTTPS redirect and automatic-upgrade setup warning in the default FortiOS GUI configuration. |
| `FOS_PKI_CA_CERTS` | unset | semicolon-separated `refname:path` PEM entries | Trusts CA certificates by typing the PEM into `config vpn certificate ca`. The object is named after the refname when given, otherwise the certificate CN. |
| `FOS_PKI_LOCAL_CERTS` | unset | semicolon-separated `refname:key_path:cert_path` entries | Installs local certificate/private-key pairs, including the SSL deep-inspection CA pair, by typing the PEM contents into `config vpn certificate local`. Both files are required. The object is named after the refname when given, otherwise the certificate CN; use an empty refname (`:key_path:cert_path`) to imply the name. |
| `FOS_PKI_LOCAL_CERT_PASS_FILES` | unset | semicolon-separated file paths | Optional password files whose contents are typed as `set password` for encrypted private keys, paired positionally with keyed `FOS_PKI_LOCAL_CERTS` entries. |
| `FOS_PKI_REMOTE_CERTS` | unset | semicolon-separated `refname:path` PEM entries | Installs remote peer certificates by typing the PEM into `config vpn certificate remote`. The object is named after the refname when given, otherwise the certificate CN. |
| `FOS_PKI_CRLS` | unset | semicolon-separated `refname:path` CRL entries | Installs CRLs as base64 bodies in `config vpn certificate crl` when that tree is available. Older releases without a CRL config tree use `execute vpn certificate crl import tftp`. The object is named after the refname when given, otherwise the file basename. |
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

The bootstrap DNS servers (`FOS_MGMT_DNS_PRIMARY` / `FOS_MGMT_DNS_SECONDARY`)
persist through startup config application. When `FOS_FORTIGUARD_HOOKS` is set
together with either DNS variable, a `config system dns` block in the startup
config is skipped with a warning so it cannot override the launcher's DNS.

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
`FOS_MGMT_DNS_SECONDARY` are configured during bootstrap and are not removed
afterwards; they persist into the captured baseline and the final
configuration.

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

## PKI Certificates

Set the `FOS_PKI_*` variables to install certificates, CAs, and CRLs at
bootstrap. All variables carry file paths only — the plugin stages the PEM
files and the launcher reads their contents; certificate, key, and password
bytes never travel through the environment.

```yaml
envs:
  FOS_PKI_CA_CERTS: "root:/lab/root-ca.pem;intermediate:/lab/intermediate.pem"
  FOS_PKI_LOCAL_CERTS: "server:/lab/server.key:/lab/server.pem;dca:/lab/dca.key:/lab/dca.pem"
  FOS_PKI_LOCAL_CERT_PASS_FILES: "/lab/server.pass"
  FOS_PKI_REMOTE_CERTS: "peer:/lab/peer.pem"
  FOS_PKI_CRLS: "issuer:/lab/issuer.crl"
```

Entries are `;`-separated and always start with a refname field followed
by `:` (`refname:rest`). The refname names the FortiOS object; an empty
refname field implies the name — the certificate CN for certificates, the
file basename for CRLs. Local certificate entries require both a private key
and certificate. A bare `key_path:cert_path` without the leading refname field
is rejected; use `:key_path:cert_path` for CN-derived naming.

- `FOS_PKI_CA_CERTS` and `FOS_PKI_REMOTE_CERTS` entries are first typed into
  their respective `config vpn certificate` trees and named by refname or CN.
- `FOS_PKI_LOCAL_CERTS` entries are typed into
  `config vpn certificate local` as quoted multi-line PEM values named by
  refname or CN. Every entry must supply its private key before its certificate.
  The same mechanism covers server certificates and the SSL deep-inspection CA
  pair.
- `FOS_PKI_LOCAL_CERT_PASS_FILES` entries pair positionally with
  keyed `FOS_PKI_LOCAL_CERTS` entries whose private keys are encrypted.
- `FOS_PKI_CRLS` entries are installed as base64 bodies when the FortiOS
  version has a CRL config tree. On older versions without that tree, each CRL
  is staged under `/tftpboot/pki/` and installed with the TFTP import command.

If config installation fails for a CA, remote certificate, or CRL, that object
is staged under `/tftpboot/pki/` and retried with `execute vpn certificate ...
import tftp`. A successful fallback logs a warning. A failed fallback aborts
bootstrap with an error. Local certificate/key pairs cannot use this fallback
because FortiOS TFTP import does not accept the configured separate key and
certificate files; their config failure therefore aborts bootstrap directly.

Certificates are installed after the factory baseline is captured and before
the startup config applies, so startup config can reference installed
certificate names. Duplicate object names within one category keep the last
entry and log a warning. Missing files abort startup with an error naming
the variable and path. Not supported: SCEP/EST/CMP enrollment, HSM
certificates, OCSP
servers, inline base64 certificate values, certificate generation, and
ssl-ssh-profile wiring.

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

Disk formatting during bootstrap adds roughly a minute per additional
drive to the time the container stays `starting`. The healthcheck absorbs
this automatically — see [Container Healthcheck](#container-healthcheck).

## Container Healthcheck

The image's `HEALTHCHECK` runs `/fos_healthcheck.py`, which replaces the
plain `/health`-file check from upstream vrnetlab. The problem it solves:
FortiOS bootstrap takes several minutes, and with Docker's default probe
timing (30 s interval, 3 retries) the container flapped
`starting -> unhealthy (1 starting) -> healthy (0 running)` purely
because the third probe landed before bootstrap finished.

How it works:

- The launcher appends one byte to `/healthbeat` on every main-loop
  iteration.
- While `/health` still says the VM is starting, the probe blocks,
  waiting for those heartbeat bytes. Docker keeps a container in
  `starting` while a probe run is in flight, so a slow-but-progressing
  bootstrap holds `starting` for as long as it likes — no fixed start
  period to size against disk count or platform.
- If no new heartbeat byte arrives for `FOS_HEALTHCHECK_STALL_SECONDS`
  (default 90 s), the launcher is wedged: the probe exits 1 and Docker
  marks the container unhealthy after `--retries` strikes.
- Once bootstrap has completed, the probe defers to the classic check:
  `0 running` exits 0 immediately, `1 VM failed - restarting` exits 1,
  so a VM that dies after startup still goes unhealthy on the normal
  probe schedule (~90 s).

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
- bootstrap feature failures stop startup by default; set
  `FOS_EXIT_ON_BOOTSTRAP_ERROR=false` only when a manually recoverable VM is
  preferred over an immediate launcher exit
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
