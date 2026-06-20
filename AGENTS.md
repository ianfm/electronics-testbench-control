# Project guidance for coding agents

## Project context

This repository controls real laboratory instruments over SCPI/VISA. Instrument
behavior is ultimately established by the relevant programming manual and by
testing against the physical hardware. Generic software conventions are not a
substitute for either source.

The Keysight B2902B and Rigol M300 are complex instruments with programming
manuals hundreds of pages long. A large driver or a subsystem-oriented design
is not inherently excessive for these devices. Preserve necessary complexity
while keeping transport, device commands, and experiment workflows separated.

## Branch context

- `main` is not necessarily the latest accepted development state.
- `dev` and active feature branches should normally be treated as the most
  recent accepted work.
- The `b2902b-subsystem` branch contains the most developed B2902B work and is
  important architectural evidence even when it has not been merged elsewhere.
- Before evaluating or changing a driver, compare the relevant implementations
  and history across `dev` and its feature branch.
- Do not merge branches wholesale without reviewing their hardware-specific
  changes and preserving experimentally validated behavior.

## Known hardware history

The following instruments have worked with this project in the past:

- Siglent SPD1305X power supply
- Rigol DM3058E digital multimeter
- Keithley digital multimeter

That history does not prove every current branch works, but existing commands
and timing choices may encode results learned from real hardware. Do not remove
or redesign them solely because they look unusual in generic application code.

## Device-interaction review standard

Do not claim that a SCPI command sequence, reset, delay, termination setting,
readback, range, trigger sequence, or connection behavior is incorrect or
unsafe without a device-specific reference. Acceptable references are, in
descending order of authority:

1. The exact instrument's programming or service manual.
2. Reproducible observations from the physical instrument.
3. Existing project notes, commit messages, or comments that explicitly record
   a hardware test.
4. Vendor documentation for the same model or firmware family.

If none is available, describe the behavior as unverified and state what manual
section or hardware experiment would resolve it. Do not replace an unverified
sequence with a more conventional one and present that as a correctness fix.

In particular, initialization commands such as `*RST` may be intentional and
experimentally necessary. Determine their provenance and device effect before
changing them. The same rule applies to apparent workarounds such as manual
query delays, raw reads, omitted readbacks, and command-specific settling time.

## Instrument documentation

Maintain the programming manuals and authoritative documentation used to
implement each instrument driver as project resources. Keep
`docs/instrument-documentation.md` current when adding a model, changing a
command sequence, locating a newer manual revision, or validating different
firmware. Do not leave the only copy of a required manual isolated on an
unmerged feature branch.

When licensing permits, keep a stable local copy under `manuals/<vendor>/` and
record its title, revision, applicable model, source URL, and repository path in
the manifest. When redistribution is not permitted, retain the authoritative
vendor URL and enough publication metadata to retrieve the exact document.

## Validation expectations

- Hardware validation is the acceptance criterion for device interaction.
- Software tests remain useful for parsing, command formatting, state handling,
  logging, and regressions that do not require an instrument.
- Mock tests must not be described as proof that hardware will accept a command
  or behave correctly.
- Record the instrument model, connection type, relevant firmware version,
  setup, commands, observed responses, and outcome when performing hardware
  validation.
- Keep hardware tests explicit. Clearly identify tests that may reset an
  instrument, change a setpoint, switch a route, or enable an output.
- A normal instrument discovery pass should complete within 10 seconds on the
  bench network. Treat repeatable runs over that limit as a performance defect
  and optimize or narrow the discovery work before adding more probes.

## Architecture direction

Keep these concerns distinct where practical:

- VISA/resource transport and response framing
- Device- and firmware-specific SCPI behavior
- Instrument subsystems and capabilities
- Experiment or bench workflows
- Data logging and analysis

Prefer model-specific drivers backed by the programming manual over assumptions
that similarly named SCPI commands are portable across vendors. Reuse should
come from demonstrated common behavior, not merely similar command spelling.
