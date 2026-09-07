# FortiGate Docker Launcher Internals

The public FortiGate/Containerlab API is documented in
[`../README.md`](../README.md). This file documents how the launcher works
inside the vrnetlab image and why some of the implementation looks unusual.

The launcher is built around serial-console automation. FortiOS does not expose
a stable early-boot API for the work this image needs to perform, so bootstrap
is modeled as three layers:

```text
serial connection -> Terminal -> FOSCliDriver -> FOSCommander -> Features
```

## Three Layer Model

### Terminal

`terminal.py` is the lowest layer. It wraps the vrnetlab telnet console and
provides buffered reads, regex matching, writes, and output suppression.

Important behavior:

- `Terminal.expect()` keeps unmatched bytes in an internal buffer across calls.
  FortiOS prompts and banners often arrive fragmented, so callers must not
  assume one read equals one logical event.
- A terminal-owned reader thread continuously drains the serial connection into
  a byte-counted, thread-safe queue. `Terminal.expect()` reads from that queue
  instead of making serial syscalls directly, so CLI state handling is not paced
  by each console read attempt. The FortiOS scrapli console exposes a blocking
  read for this thread; nonblocking eager reads remain only as a fallback for
  simpler connection wrappers.
- `Terminal.write()` queues bytes for a terminal-owned writer thread instead of
  writing to the serial connection directly. The writer preserves write order
  and sends one queued chunk at a time, so commander/driver state handling is
  not paced by each scrapli write call.
- `Terminal.expect()` returns a `Data` object, not raw bytes. `Data` is a byte
  view of what was just read or matched, and timeout `Data` can discard bytes
  from the terminal's retained buffer.
- When multiple regexes match buffered output, the match that ends earliest
  wins. This avoids later, broader prompt patterns swallowing earlier prompts.
- Matched bytes are consumed from the buffer; bytes after the match stay queued
  for the next state parse.
- Timeout bytes are not consumed automatically. A command, feature, or commander
  inspection path must call `data.discard(...)` if it has assigned meaning to
  those bytes. Otherwise the bytes stay in the terminal buffer and can be
  matched again when later output completes a prompt or state.
- Retained terminal data is bounded by `FOS_TERMINAL_BUFFER_LIMIT_BYTES`, which
  defaults to 1 MiB. If the buffer grows beyond that limit, bootstrap fails
  instead of truncating data, because oversized retained output means some layer
  failed to consume bytes it understood.
- The same limit bounds unread data in the reader queue and unsent data in the
  writer queue. When queued serial data reaches the limit, the reader or writer
  thread waits until the corresponding consumer drains queue bytes.
- `suppress_output()` temporarily prevents machine-readable commands such as
  `show` and `get system status` from being mirrored to the visible serial log,
  while still keeping their output available to the parser.
- `wait_write()` is legacy-compatible convenience glue. New bootstrap logic
  generally goes through `FOSCommander`, but `Terminal` still owns the raw IO
  primitives.

The terminal layer does not understand FortiOS. It only knows bytes, regexes,
and writes.

### State Parsing

`fos_cli_driver.py` translates terminal output into `FOSCliState` events. It
owns login and interactive prompt handling:

- username prompt
- password prompt
- first-login password change
- current-password prompt after admin password changes
- credential rejection and blank-password fallback
- `Welcome!`
- command prompt
- reboot and shutdown messages
- license failure
- `--More--` pagination
- unknown output and timeout recovery

The driver runs in short spins. Each spin reads a state from `Terminal.expect()`,
handles login/pager responses itself, and then tells `FOSCommander` what state
was observed. Command dispatch is deliberately not done directly in the driver;
the driver only answers FortiOS protocol prompts and feeds state to the
dispatcher.

The driver does not decide whether unknown or prompt-echo output is meaningful.
It passes `Data` to the commander. If the downstream command, feature, or
commander inspection path discards it, the terminal buffer is updated through
the `Data` object. If it is not discarded, the bytes remain buffered.

There are two prompt-pattern phases:

- Before license and hostname changes, prompt matching accepts broad bootstrap
  hostnames because FortiOS can rename itself or switch to a licensed serial
  style.
- After hostname setup, the driver narrows login and command prompt patterns to
  the configured node hostname.

Unknown output is treated as a stall only after it keeps arriving without a
known state. The driver sends a small number of newlines to recover prompts,
then fails if FortiOS stays unparseable.

### Command Dispatcher

`fos_commander.py` schedules feature commands. It is the only layer that knows
which bootstrap feature is active and which command is in flight.

Commands are represented by:

- `CommandSpec`: one FortiOS CLI line plus execution flags
- `ConfigBlock`: emits `config <scope>`, child commands, and `end`
- `EditBlock`: emits `edit <name>`, child commands, and `next`
- `CommandSequence`: emits child commands without config framing

Plain string commands are automatically wrapped in a default `CommandSpec`
when submitted directly or used inside any command container. Use an explicit
`CommandSpec` only when a command needs non-default execution flags.

`CommandSpec` intentionally has no separate name field. The command line is the
identity, and feature state machines track phase when they need context.

The dispatcher sends one command at a time and waits for an appropriate state
before continuing. By default, a command completes at the next command prompt.
Some commands override completion states, for example confirmation prompts or
reboot messages.

Session loss is explicit. Each command carries a `SessionLossAction`:

- `RESTART_BLOCK`: replay the current block from the start
- `COMPLETE_BLOCK`: treat the block as complete
- `CONTINUE`: do not replay the disruptive command
- `VALIDATE`: reserve for commands that need post-loss validation
- `FAIL`: raise immediately

This matters because commands such as license restore and password updates can
drop the active admin session. Replaying them blindly can corrupt state or loop.

The dispatcher also manages cleanup actions and standard-output contexts.
Features can request console output mode `standard` for machine-readable output;
the dispatcher detects the current console mode, changes it only when needed,
and restores `more` after the feature completes.

## Output Handling

All CLI output passed into `FOSCommander` is `terminal.Data`. The commander does
not accept raw bytes and does not own output buffering. The output path is:

```text
Terminal.expect() -> Data -> FOSCliDriver -> FOSCommander
```

When output arrives while a command is in flight, the commander offers it in
this order:

```text
CommandAttempt.on_output(data)
Feature.on_output(data)
FOSCommander._inspect_output(data)
```

The first layer that understands the data consumes it by calling
`data.discard(...)`. Consumption has two effects:

- the consumer records whatever state it needs, such as command output
- the terminal buffer discards the consumed bytes when the `Data` object is a
  timeout view into retained terminal data

Command output capture belongs to `CommandAttempt`. If a command has
`capture_output=True`, `CommandAttempt.on_output(data)` appends
`bytes(data)` to `command.output` and discards those bytes. When the command
finishes, the feature receives the completed command:

```text
on_command_executed(command, state)
```

Features inspect `command.spec` to know which command completed and
`bytes(command.output)` to inspect captured output. Features should not
reconstruct command output from commander state.

`Feature.on_output(data)` remains for out-of-band streams. For example, a
feature may start a debug command and wait for asynchronous data that is not the
normal command completion transcript. If the feature recognizes that data, it
must call `data.discard(...)` before returning true. If it does not recognize
the data, it returns false and the commander can inspect it or leave it buffered.

Prompt echo uses the same path. `CMD_PROMPT_ECHO` is not special-cased with a
separate parser in the commander. The driver passes the prompt-echo `Data` to
`FOSCommander.on_prompt_echo()`, which calls `on_output(data)`. If any layer
discards the data, no recovery newline is sent. If no layer consumes it, the
commander sends a newline to request a clean prompt.

This is the critical invariant: output that has been assigned meaning must be
discarded through `Data`; output that is not understood must remain buffered so
it can be completed by later serial data.

The retained terminal buffer has a hard upper bound. The default is 1 MiB, set
with `FOS_TERMINAL_BUFFER_LIMIT_BYTES` when a larger diagnostic window is needed.
The limit is intentionally enforced as an error rather than a ring buffer: if
retained data grows without being consumed, the command/feature/commander stack
has lost ownership of some output and continuing would risk replaying stale
bytes into later state decisions.

## Feature Architecture

Bootstrap work is split into feature objects under `features/`. A feature has a
name, lifecycle hooks, optional file-watch integration, and zero or more command
blocks.

The core lifecycle is:

```text
begin_activation()
 promactivate()
on_command_dispatched(attempt)
on_output(output)
on_command_executed(command, state)
on_block_complete()
on_session_loss(attempt)
mark_completed()
```

Static command features subclass `StaticFeature`; they submit a fixed list of
blocks and complete after the last block. Dynamic features subclass `Feature`
and submit follow-up blocks based on command output or timer ticks.

Current bootstrap stages are:

- `disk-format`: formats additional disks after the first FortiGate log disk
- `admin`: disables password policy, creates/updates the desired admin, and
  handles session loss during password changes
- `management`: configures `port1`, FortiGuard interface selection, and the
  management route for static management addressing
- `bootstrap-dns`: temporarily sets DNS for license/bootstrap reachability
- `setup-license`: restores `/tftpboot/appliance.lic` over TFTP and handles reboot
- `default-config`: applies launcher-owned defaults such as `admin-scp` and the
  final hostname
- `management-after-license`: reapplies management after license restore because
  FortiOS can remove routes or drop sessions when registration state changes
- `license-validation`: polls `get system status` until license status is no longer
  `Pending`, or until the configured timeout
- `management-vrf`: moves management into VRF 1 when supported, or narrows
  the management route on FortiProxy
- `undo-bootstrap-dns`: removes the temporary bootstrap DNS settings
- `fortitoken-provisioning`: after a license becomes `VALID`, polls for
  license-provisioned FortiTokens for up to 15 seconds
- `capture-config`: records a clean baseline and later services `/get-config`
  runtime captures
- `startup-config`: imports the user-supplied startup config

`FeatureFileWatcher` polls watched feature paths after bootstrap. Today the
main runtime feature is config capture: touching `/get-config` enqueues the
capture feature after the launcher reconnects to the serial console.

The feature architecture is intentionally serial. FortiOS CLI state is global,
the console has one prompt stream, and several operations can reboot or remove
admin sessions. Parallel feature execution would make recovery ambiguous.

## Design Quirks

### Password handling is deliberately conservative

FortiOS versions differ in default credential behavior. Some accept the default
password, some require first-login password changes, and some older paths accept
a blank default password first. The driver keeps separate bootstrap and desired
credentials so it can answer prompts with the password that is valid at that
moment.

The desired credentials are activated only after FortiOS accepts the password
change or after the admin edit commits. This avoids answering a current-password
prompt with a password FortiOS has not accepted yet.

### Prompt patterns change during bootstrap

License installation and hostname changes can alter prompt text. During license
restore the driver broadens prompt matching; after `set hostname`, it narrows
patterns to the configured hostname. This is why prompt handling lives in the
driver rather than directly in features.

### Console pagination is not globally disabled forever

Machine-readable commands need unpaginated output, but interactive users expect
FortiOS's normal paginated console. The commander temporarily switches to
`standard` output for captures and polls, then restores `more` if it changed
the setting.

### Config capture is delta-based

The launcher captures a baseline before user startup config. Later `/get-config`
captures run `show`, clean terminal artifacts, parse config/edit blocks, and
write only changes to `/config/current.conf`.

Encrypted fields are noisy because FortiOS can emit different `ENC` values for
the same effective secret. The diff code can ignore ENC-only changes for
existing entries when configured, but it still retains encrypted fields for new
entries or entries with other body changes.

### Feature names are workflow controls, not command labels

Feature names are used for logging, debug cutoffs, and undo stages. Individual
commands are identified by their CLI line. When a workflow needs to distinguish
steps, it tracks its own phase instead of attaching synthetic names to commands.

## Gotchas And Required Hoop-Jumps

- Bootstrap must run through the serial console. SSH is not reliable until
  credentials, management, license state, and prompt patterns settle.
- License restore can reboot FortiOS and can remove the active admin session.
  The `setup-license` feature marks those commands as non-replayable and waits
  for the next prompt instead.
- Management configuration is applied more than once. License registration can
  disturb routes and sessions, so `management-after-license` repairs management
  before license polling.
- FortiProxy does not support interface VRFs in the same way FortiGate does.
  `management-vrf` detects `set vrf 1` failures and falls back to a
  management-gateway route destination instead of installing a broad default
  route.
- TFTP needs special handling in both management modes. The license file is
  restored from `/tftpboot/appliance.lic`; the VM must be able to reach the
  launcher TFTP service from its management interface during bootstrap.
- The QEMU management MAC can come from snapshot metadata or vrnetlab's generated
  MAC. In passthrough mode the container `eth0` MAC is changed to the VM
  management MAC so redirected management traffic looks consistent.
- The launcher writes files inside `/config` and the node config mount. These
  files are part of runtime behavior, not source artifacts.
- The Makefile copies only regular files from `../../common` into this directory
  during image build. Python cache directories under `common/` must not enter
  the Docker build context as copied launcher assets.

## Management Networking

The management strategy is selected before the VM is created. Both strategies
implement `NetMgmtStrategy`, which supplies QEMU `-netdev` arguments and writes
strategy-specific host/container networking.

### Host-Forwarded Mode

`HostForwardedBridge` is used when management passthrough is disabled. It
creates an internal bridge named `br-mgmt` and gives the FortiGate a point to
point management network:

```text
container bridge: 172.31.255.29/30, 200::/127
FortiGate port1: 172.31.255.30/30, 200::1/127
```

QEMU attaches `port1` with:

```text
-netdev bridge,br=br-mgmt,id=p00
```

The container installs NAT rules:

- inbound TCP on `eth0`, except serial port `5000`, is DNATed to the FortiGate
  management address
- inbound UDP on `eth0` is DNATed to the FortiGate management address
- traffic leaving `br-mgmt` is masqueraded so FortiOS replies through the bridge
- traffic from FortiOS out through `eth0` is masqueraded for breakout

IPv6 forwarding is enabled in the container and equivalent IPv6 NAT rules are
installed. The bridge is allowed through QEMU bridge configuration with
`/etc/qemu/bridge.conf`.

In this mode the TFTP server runs in the container's normal namespace. UDP DNAT
lets FortiOS reach it through the host-forwarded management path.

This mode keeps Containerlab's management network connected to the container,
not directly to FortiOS. It is easier to reason about as NAT/port-forwarding,
but it means all management services except serial are forwarded through the
container.

### Passthrough Mode

`PassthroughRedirect` is the default mode. It makes FortiGate `port1` appear to
participate directly in the Containerlab management network.

QEMU attaches `port1` with a tap:

```text
-netdev tap,ifname=tap0,id=p00,script=/etc/tc-tap-mgmt-ifup,downscript=no
```

The generated ifup script:

- brings up `tap0`
- disables IPv6 addresses on `tap0` to avoid container-originated IPv6 chatter
- adds `clsact` qdiscs to `eth0`, `tap0`, and the redirect veth
- passes TCP ports `5000-5007` so serial consoles stay on the container
- mirrors ARP from `eth0` to `tap0`
- redirects most ingress `eth0` traffic to `tap0`
- redirects ingress `tap0` traffic back to `eth0`
- changes container `eth0` to the VM management MAC

Passthrough mode also creates a private namespace named `fakehost` with a veth
pair:

```text
root namespace: RA
fakehost namespace: FA
```

The namespace exists so launcher-owned services, currently TFTP, can still live
"beside" the VM even though normal management traffic is redirected straight
between the Containerlab management network and the VM tap.

The namespace starts with a temporary address so services can bind before QEMU
calls the tap ifup script. During ifup, the temporary address is replaced with
the management gateway address that FortiOS expects. `tc flower` rules redirect
selected service ports from the VM to the namespace and rewrite Ethernet
destinations so the veth path works:

- VM -> namespace traffic is matched on protocol, destination port, and gateway
  IP, then redirected from `tap0` to `RA`
- namespace -> VM return traffic is matched on source port and FortiGate
  management IP, then redirected back to `tap0` with the VM MAC

The TFTP server runs inside `fakehost` in passthrough mode, with a fixed TID
port range so the flower rules can match both the initial TFTP request and
server-selected data ports.

Passthrough gives the most natural Containerlab management behavior, but it is
the most fragile path: MAC rewriting, tap setup timing, namespace addressing,
and tc filter order all need to line up before license restore can work.
