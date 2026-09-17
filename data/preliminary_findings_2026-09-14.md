# Preliminary findings — charging a dead pack from emulated solar (2026-09-14)

**Status: run still in progress (overnight). These are observations from the first ~7 minutes, not conclusions.**

## Setup
- DUT: solar MPPT charger board, power-floor firmware (all loads off).
- Battery: real protected LiFePO4 pack, 1350 mAh, 3.3 V nominal. Over-discharged; protection FET latched open → terminals read 0 V.
- Solar input: B2902B CH1 replaying the measured panel curve `panel-iv-2026-08-24_145423` (Voc 7.71 V, Isc 0.193 A, Pmax 1.10 W at 6.25 V). Source parked at Voc, compliance re-derived from achieved voltage continuously — the charger sets the operating point.
- Battery current: B2902B CH2 in series with battery+, 0 V source (zero-burden ammeter), 3 A range, no effective compliance (3 A instrument max). V_bat: 34461A directly across the pack.
- Sequence: solar on first, battery loop second.

## Observations (t = 0 → 400 s, unchanged throughout)
| quantity | value | reading |
|---|---|---|
| solar node | 7.711 V, 0.45 mA, 3.5 mW | charger idle at Voc — standby draw only |
| battery current | ≈ 0 (−0.1 mA, below 3 A-range floor) | no charge current |
| V_bat | 0.9 mV | protection still latched |

No boot activity, no charge attempt, no periodic retry visible on the input side.

## Interpretation (tentative)
Mutual standoff: the charger sees 0 V on its battery terminal (protection open) and does not start — typical battery-absent / no-precharge behaviour. The pack's protection IC will only re-close when it sees a charge voltage at its terminals, which the charger is not providing. Neither side moves first.

Supporting evidence: earlier today the same pack woke immediately (terminals → 2.99 V) when ~1 mA at ~3 V was briefly presented at its terminals by a DMM ohms measurement. It only needs *some* voltage to unlatch.

## Fixture notes
- The emulated panel holds its setpoint under measurement (7.712 V read vs 7.711 V set) after pinning the B2900 triggered source level — the same bug the August bench code documents. Earlier anomalous readings today (negative input current, ~6 V) were this fixture bug, not the board.
- CH2 at 0 V and the DMM are passive; nothing in the fixture limits or drives the charger.
- Limitation: on the 3 A range the battery path cannot resolve below ~0.5 mA. A µA-level precharge would be invisible on CH2 but would show on CH1 as input power rising above the 3.5 mW standby.

## Overnight result (run ended 2026-09-15, 8 h wall-clock limit)
14,401 samples. **No change at any point in 8 hours**: solar node 7.71 V, 0.34–0.47 mA, 2.6–3.6 mW the entire time; V_bat 0.6–1.0 mV (protection never unlatched); battery current within the 3 A-range noise floor. No retry, no precharge attempt, no boot activity. Total energy drawn from the emulated panel: 0.028 Wh. Panel replay loop ran at 10.5 Hz with 1 compliance update — the charger never moved the input off Voc. Both outputs off at exit, error queues clean.

Enable-pin check (schematic ON1006 v1.2.0, Power sheet): MCU PB1 (`PWR_SHDN`) drives the charger enable through an inverting NPN (Q8) with the enable pin pulled up to Vin via R81 10 k. PB1 low or floating = charger **enabled**; only PB1 high disables. The flashed power-floor firmware never configures PB1. Firmware is not what holds the LTC3130 off — unless the LTC3130 revision removed Q8/R81 (schematic for that revision not available).

## Cross-check (2026-09-15)
The LT3652 (previous charger) board puts ~2.9 V on the battery terminals immediately when solar is applied. The LTC3130 board does not, under identical input. **Conclusion: circuit problem on the LTC3130 revision** — not firmware, not the pack, not the fixture.

## Root cause of the fixture failure (2026-09-15) — SMU LOW terminals grounded
B2900 `:OUTP[c]:LOW` resets to **GROund**: the LOW terminal is connected to chassis ground, on both channels, after every `*RST` (the driver issues one on connect). CH1 LO was on board GND and CH2 LO (series ammeter) was on the board's battery+ pin, so **the instrument chassis shorted battery+ to GND**. This single fact explains every fixture anomaly: the "0.6 Ω copper path to ground" at the CH2 LO clip, the 1.35 A / 10 mA "battery discharge" loops (battery+ → CH2 → chassis → CH1 LO → board GND → battery−), the pack terminals stuck at ~0.1 V while VOUT read 3.48 V, the charger dissipating 0.5 W into a shorted output, and the immediate recovery once CH2 was removed from the loop. Fix: `:OUTP1:LOW FLO` / `:OUTP2:LOW FLO` while outputs are off (now in all three scripts). The board and firmware were never at fault for this part; the bridge-resistor repair was a separate, real issue.

**Result with CH2 out of the loop (stiff 7.71 V / 0.194 A source, pack latched at 0 V):** pack terminals jumped to 2.72 V on the first sample and climbed steadily; CH1 in current limit at 193.5 mA / 6.10 V = 1.18 W — the charger pulls the panel's full available power. **The repaired LTC3130 board recovers a protection-latched pack from 0 V and charges it from a panel-limited source.**

## Next
1. Overnight run: does the charger ever retry / precharge on its own? (Run auto-stops at 8 h; data in `data/charge_log_first_charge.csv`.)
2. If not: wake the pack externally (brief ~3 V at a few mA), then observe whether the charger takes over once battery voltage is present. Separates "won't start on a latched pack" from "won't charge at all".
3. Compare against August runs, which booted the board from an emulated 3.3 V battery and never faced a dead cell.
