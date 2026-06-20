# Instrument discovery

Instrument discovery is not a single protocol. VISA enumeration can return USB,
VXI-11, and HiSLIP resources, but it cannot be assumed to find every reachable
instrument. This project therefore treats discovery as a sequence of independent
methods followed by a read-only `*IDN?` identity check.

Run discovery with:

```bash
testbench discover
```

The legacy command remains available as a wrapper:

```bash
python3 examples/list_devices.py
```

Both commands identify instruments by default. They do not reset, configure,
trigger, route, or enable an instrument output.

## Discovery sequence

1. Probe resource strings saved by the previous successful run.
2. Probe resources returned by the active VISA backend.
3. Determine active, directly connected IPv4 subnets.
4. Probe those subnets for VXI-11 portmapper TCP port 111 and raw SCPI ports,
   initially TCP 5025.
5. Attempt explicit `TCPIP::<address>::INSTR` access for VXI-11 candidates.
6. Attempt explicit `TCPIP::<address>::<port>::SOCKET` access for raw SCPI
   candidates.
7. Send `*IDN?` and combine endpoints that report the same manufacturer and
   serial number.
8. Save successful resource strings for the next run.

Network scanning is limited to 1,024 usable hosts per subnet by default. Larger
networks are skipped and reported. Specify a narrower network explicitly when
needed:

```bash
testbench discover --subnet 192.168.86.0/24
```

The operational performance budget is 10 seconds for a normal discovery pass
on the bench network. Repeatable runs above that limit require profiling and
optimization or a narrower scan configuration. With the four-instrument bench
on 2026-06-19, a full uncached `/24` pass took 5.38 seconds and a cache-only pass
took 2.31 seconds.

Use `--show-failures` to retain errors from endpoints that were visible but did
not answer `*IDN?`. Use `--no-network-scan` when network probing is undesirable.
Additional raw SCPI ports can be supplied with repeated `--raw-scpi-port`
arguments.

## Observed failure modes

These are observations from this bench, not assumptions about every instrument.

### VXI-11 broadcast omission

On 2026-06-19, PyVISA-py VXI-11 discovery returned the Rigol M300 at
`192.168.86.170` but omitted the Siglent SPD3303X-E at `192.168.86.169`.
PyVISA-py performs VXI-11 discovery by broadcasting a UDP portmapper request and
collecting replies after a fixed one-second wait. The exact reason the Siglent
did not appear in that response has not been established.

Direct access proved that the omitted Siglent endpoint worked:

```text
TCPIP::192.168.86.169::INSTR
Siglent Technologies,SPD3303X-E,SPD3XJGQ903866,1.01.01.03.10,V6.2
```

Resolution: scan the local subnet for port 111 and try explicit VXI-11 resources
even when broadcast discovery omits them.

### Raw TCP resources are not enumerated

PyVISA-py's `TCPIP SOCKET` discovery implementation returns no resources because
there is no general discovery protocol for arbitrary TCP sockets. The same
Siglent answered on raw SCPI port 5025 when addressed explicitly.

Resolution: probe configured raw SCPI ports and validate candidates with
`*IDN?`. Port 5025 is the default, not a claim that all instruments use it.

### Neighbor tables are incomplete

The Siglent was absent from the host's ARP/neighbor table before direct contact.
Scanning only cached neighbors therefore missed a powered, reachable device.

Resolution: enumerate every host in an explicitly bounded, directly connected
subnet rather than using the neighbor cache as the candidate list.

### USB permissions prevent enumeration

The Keithley 2110 appeared in `lsusb` and as `/dev/usbtmc1`, but PyUSB could not
read its descriptors until vendor `05e6` was included in the installed udev
rules and the instrument was replugged.

Resolution: maintain vendor rules in `install/99-visa.rules`, install them with
`install/setup_system.sh`, and report VISA enumeration errors. Replugging is
required for new udev permissions to apply to an existing device.

### Transient transport errors

The Rigol DM3058E returned a USB pipe error during one discovery pass and
answered normally on a later pass without a configuration command.

Resolution: identity probing opens a fresh resource and retries. It does not
send `*CLS`, `*RST`, a device clear, or another state-changing recovery command.

### Multiple interfaces and large networks

Hosts may have Ethernet, Wi-Fi, VPN, container, and link-local interfaces.
Blindly scanning all address space is slow and can reach networks outside the
bench.

Resolution: scan only active, directly connected IPv4 networks that fit the
host limit. Skip loopback and link-local networks. Operators can disable scans,
set an explicit subnet, or reduce the host limit.

### Serial, GPIB, gateways, and vendor protocols

No universal probe covers serial instruments, GPIB controllers, Prologix
gateways, VICP, or vendor-specific transports. Serial probing can also send
bytes to unrelated serial devices.

Resolution: keep discovery providers separate and add protocol-specific
providers backed by hardware evidence. VISA-reported serial resources are
excluded unless `--include-serial` is explicitly supplied.

## What discovery establishes

A successful result establishes only that a resource was reachable and returned
a recognizable `*IDN?` response during that run. It does not establish that the
instrument driver is correct, that later commands will work, or that the device
is safe to use in a particular experiment. Those claims require the programming
manual and hardware validation described in `AGENTS.md`.

The cache is an optimization, not an authority. Cached resources are probed and
must answer again. DHCP changes, disconnected devices, and stale entries are
therefore visible as failed attempts rather than accepted identities.
