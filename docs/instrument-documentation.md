# Instrument documentation manifest

This manifest records the model-specific references used for SCPI behavior.
Documentation should be reviewed alongside hardware observations; neither a
generic SCPI convention nor a different model's manual is sufficient evidence
for changing device interaction.

## Current instruments

### Rigol DM3058E

- Reference: `DM3058 Programming Guide`
- Existing repository copy: `doc/DM3058_ProgrammingGuide_EN.pdf` on
  `origin/rigol_m300`
- Current-branch status: not present
- Verified use: `MEASure:VOLTage:DC?` accepted by serial `DM3R232201369` on
  2026-06-19

### Keithley 2110

- Reference: `Model 2110 5 1/2 Digit Multimeter Reference Manual`
- Part number/revision: `2110-901-01 Rev. C`
- Vendor PDF:
  <https://download.tek.com/manual/2110-901-01%28C-Aug2013%29%28Ref%29.pdf>
- Current-branch status: not present locally
- Verified use: `MEASure:VOLTage:DC?` accepted by serial `8009937`, firmware
  `02.02-02-01`, on 2026-06-19

### Rigol M300

- Reference: `M300 Programming Guide`
- Existing repository copy: `doc/M300_ProgrammingGuide_EN.pdf` on
  `origin/rigol_m300`
- Official product documentation:
  <https://www.rigolna.com/products/data-acquisition/m300-system/>
- Current-branch status: not present
- Verified configuration: MC3065 DMM in slot 1, MC3132 multiplexers in slots 2
  and 4, and MC3416 actuator modules in slots 3 and 5
- Verified use: `SYSTem:CTYPe?`, `INSTrument:DMM?`,
  `CONFigure:VOLTage:DC`, `ROUTe:SCAN`, `READ?`, and `ROUTe:OPEN?` accepted by
  serial `MM3A203500090`, firmware `00.02.00.08.00`, on 2026-06-19

### Siglent SPD3303X-E

- Reference: `SPD3303X/3303X-E Quick Start`, remote-control chapter
- Vendor PDF:
  <https://siglentna.com/wp-content/uploads/dlm_uploads/2022/11/SPD3303X_QuickStart_E02A.pdf>
- Current-branch status: not present locally
- Verified use: `MEASure:VOLTage? CH1`, `MEASure:CURRent? CH1`, and
  `MEASure:POWEr? CH1` accepted by serial `SPD3XJGQ903866`, firmware
  `1.01.01.03.10,V6.2`, on 2026-06-19

## Other implemented instruments

### Siglent SPD1305X

- Current-branch status: no model-specific manual is tracked
- Required follow-up: locate the exact vendor programming reference and record
  the hardware/firmware revision used for the existing command workarounds

### Keysight B2902B

- Reference: `B2900A Precision Source-Measure Unit SCPI Command Reference`
- Existing repository copy:
  `manuals/B2900A Precision Source-Measure Unit SCPI Command Reference.pdf` on
  `origin/b2902b-subsystem`
- Current-branch status: not present
- Required follow-up: preserve the exact publication revision when bringing the
  B2902B subsystem branch forward

## Maintenance rules

- Record exact model names and manual revisions; do not silently substitute a
  related model's documentation.
- Record firmware versions with hardware observations.
- Cite the command or section used when implementing new device behavior.
- Preserve documentation provenance when moving work between branches.
- Confirm that redistribution is permitted before committing vendor PDFs.
- Update this manifest when a source URL changes or a local manual is added.
