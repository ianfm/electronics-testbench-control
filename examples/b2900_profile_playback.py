"""Curve-following solar-profile playback on a B2902B against an MPPT DUT.

Replays real panel I-V curves captured by ``examples/log_solar_sweep.py``.
For each captured sweep, CH1 emulates that curve for a dwell period using a
software feedback loop (measure the current the MPPT charger draws, look up
the voltage a real panel would sit at for that current, relax the CH1 source
voltage toward it), while CH2 emulates the battery at a fixed voltage.

Offline verification: ``--parse-only`` loads the capture, prepares every
curve, prints the per-sweep table and exits WITHOUT opening any VISA
resource (the KeysightB2902B object is never constructed on that path).

Safety invariants match examples/b2900_charge_efficiency.py:
  * Refuses --vbat above VBAT_ABS_MAX (3.55 V).
  * HIZ output-off mode on both channels before any output-on.
  * Asymmetric compliance: CH1 PROT:POS = 1.1*Isc of the active sweep
    (rectangle backstop) / PROT:NEG small; CH2 PROT:POS small / PROT:NEG =
    charge limit (--ch2-neg-limit; take the value discovered by
    b2900_charge_efficiency.py's spot-check on the same DUT).
  * CH2 (battery) on first / off last; CH1 on second / off first, each with
    a 50 mV readback abort (open sense lead detector).
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RESOURCE = "USB0::10893::37377::MY60440156::0::INSTR"

VBAT_ABS_MAX = 3.55  # hard refusal ceiling for the battery-side setpoint
READBACK_TOL_V = 0.050
SENTINEL_ABS = 1e30  # SMU NaN sentinel (~9.9e37) filter threshold
MIN_CURVE_POINTS = 10
LOW_POWER_W = 0.05  # sweeps below this Pmp are "dark"
V_SET_FLOOR = 0.2  # lower clamp for the CH1 source voltage
MIN_TICK_RATE_HZ = 5.0
UNSTABLE_RATIO = 0.1

# B2902B DC current measurement ranges (A).
CURRENT_RANGES = (1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 3.0)

DEFAULT_DWELL_S = 20.0
DEFAULT_ALPHA = 0.4
DEFAULT_NPLC = 0.2
DEFAULT_TICK_INTERVAL = 0.05
DEFAULT_VBAT = 3.3
DEFAULT_CH2_POS_LIMIT = 0.05
DEFAULT_CH2_NEG_LIMIT = 1.0
DEFAULT_CH1_NEG_LIMIT = 0.01
DEFAULT_TIMEOUT_MS = 30000


# --------------------------------------------------------------------------
# Curve preparation (pure functions -- testable offline via --parse-only)
# --------------------------------------------------------------------------

@dataclass
class SolarCurve:
    """One captured I-V sweep prepared for playback."""

    index: int  # playback ordinal (1-based, file order)
    capture_sweep: int  # sweep column value from the capture CSV
    timestamp: str
    elapsed_s: float
    points: list  # kept (v, i) pairs, v ascending, i >= 0
    dropped: int
    voc: float
    isc: float
    vmp: float
    imp: float
    pmp: float
    lookup_i: list = field(default_factory=list)  # descending current knots
    lookup_v: list = field(default_factory=list)  # matching ascending voltages

    @property
    def kept(self) -> int:
        return len(self.points)

    @property
    def low_power(self) -> bool:
        return self.pmp < LOW_POWER_W

    def v_panel(self, current: float) -> float:
        """Voltage a real panel would sit at when sourcing ``current``.

        Interpolates the monotone region of the captured I->V relation,
        clamped to I in [0, Isc] and V in [0, Voc].
        """
        i = min(max(current, 0.0), self.isc)
        knots_i, knots_v = self.lookup_i, self.lookup_v
        if not knots_i:
            return self.voc
        if i >= knots_i[0]:
            return min(max(knots_v[0], 0.0), self.voc)
        if i <= knots_i[-1]:
            return min(max(knots_v[-1], 0.0), self.voc)
        for k in range(len(knots_i) - 1):
            hi, lo = knots_i[k], knots_i[k + 1]
            if hi >= i >= lo:
                if hi == lo:
                    v = knots_v[k]
                else:
                    frac = (hi - i) / (hi - lo)
                    v = knots_v[k] + frac * (knots_v[k + 1] - knots_v[k])
                return min(max(v, 0.0), self.voc)
        return self.voc


def load_capture_rows(path: Path) -> list:
    """Parse a log_solar_sweep.py capture CSV into raw row dicts (file order)."""
    rows = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            try:
                rows.append(
                    {
                        "sweep": int(raw["sweep"]),
                        "timestamp": raw["timestamp"],
                        "elapsed_s": float(raw["elapsed_s"]),
                        "volts": float(raw["volts"]),
                        "amps": float(raw["amps"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue  # malformed line
    return rows


def group_sweeps(rows: list) -> list:
    """Split rows into per-sweep groups.

    Capture logs are appended across runs, so sweep indices can restart
    (data/mpp.csv contains several runs that all begin at sweep 1). A new
    group starts whenever the sweep column OR the per-sweep timestamp
    changes between adjacent rows (not a plain group-by).
    """
    groups = []
    current = None
    for row in rows:
        if (
            current is None
            or row["sweep"] != current["sweep"]
            or row["timestamp"] != current["timestamp"]
        ):
            current = {
                "sweep": row["sweep"],
                "timestamp": row["timestamp"],
                "elapsed_s": row["elapsed_s"],
                "points": [],
            }
            groups.append(current)
        current["points"].append((row["volts"], row["amps"]))
    return groups


def prepare_curve(group: dict, index: int) -> SolarCurve | None:
    """Turn one raw sweep group into a playback-ready SolarCurve.

    Sign convention: the panel sources into the SMU, so panel current is
    I = -amps. Sentinel (~9.9e37) and non-finite values are dropped; rows
    with I < 0 (SMU driving the panel, beyond Voc) are used only for the
    zero-crossing Voc interpolation and then dropped.
    """
    cleaned = []
    dropped = 0
    for volts, amps in group["points"]:
        if not (math.isfinite(volts) and math.isfinite(amps)):
            dropped += 1
            continue
        if abs(volts) > SENTINEL_ABS or abs(amps) > SENTINEL_ABS:
            dropped += 1
            continue
        cleaned.append((volts, -amps))
    cleaned.sort(key=lambda p: p[0])

    # Voc: interpolate the last I zero-crossing while negatives still exist.
    voc = None
    for k in range(1, len(cleaned)):
        v_a, i_a = cleaned[k - 1]
        v_b, i_b = cleaned[k]
        if i_a > 0.0 >= i_b:
            voc = v_a + (v_b - v_a) * i_a / (i_a - i_b)

    kept = [(v, i) for v, i in cleaned if i >= 0.0]
    dropped += len(cleaned) - len(kept)
    if len(kept) < MIN_CURVE_POINTS:
        return None
    if voc is None:
        voc = max(v for v, _ in kept)
    voc = max(voc, max(v for v, _ in kept))

    isc = max(i for _, i in kept)
    vmp, imp, pmp = 0.0, 0.0, 0.0
    for v, i in kept:
        if v * i > pmp:
            vmp, imp, pmp = v, i, v * i

    # Monotone I->V lookup: walk V ascending keeping strictly decreasing I,
    # then anchor the open-circuit end at (Voc, I=0).
    lookup_i: list = []
    lookup_v: list = []
    for v, i in kept:
        if not lookup_i or i < lookup_i[-1]:
            lookup_i.append(i)
            lookup_v.append(v)
    if lookup_i and lookup_i[-1] > 0.0:
        lookup_i.append(0.0)
        lookup_v.append(voc)

    return SolarCurve(
        index=index,
        capture_sweep=group["sweep"],
        timestamp=group["timestamp"],
        elapsed_s=group["elapsed_s"],
        points=kept,
        dropped=dropped,
        voc=voc,
        isc=isc,
        vmp=vmp,
        imp=imp,
        pmp=pmp,
        lookup_i=lookup_i,
        lookup_v=lookup_v,
    )


def prepare_curves(path: Path) -> list:
    """Load a capture CSV and prepare every sweep; warns on skipped sweeps."""
    groups = group_sweeps(load_capture_rows(path))
    curves = []
    for ordinal, group in enumerate(groups, start=1):
        curve = prepare_curve(group, ordinal)
        if curve is None:
            print(
                f"WARNING: sweep #{ordinal} (capture index {group['sweep']}, "
                f"{group['timestamp']}) skipped: fewer than {MIN_CURVE_POINTS} "
                "valid points after filtering"
            )
            continue
        curves.append(curve)
    return curves


def curve_flags(curve: SolarCurve) -> str:
    return "LOW_POWER" if curve.low_power else ""


def print_curve_table(curves: list) -> None:
    header = (
        f"{'idx':>4} {'capt':>4} {'timestamp':<20} {'kept':>5} {'drop':>5} "
        f"{'Voc(V)':>8} {'Isc(A)':>8} {'Vmp(V)':>8} {'Imp(A)':>8} {'Pmp(W)':>9} flags"
    )
    print(header)
    print("-" * len(header))
    for c in curves:
        print(
            f"{c.index:>4} {c.capture_sweep:>4} {c.timestamp:<20} {c.kept:>5} "
            f"{c.dropped:>5} {c.voc:>8.3f} {c.isc:>8.4f} {c.vmp:>8.3f} "
            f"{c.imp:>8.4f} {c.pmp:>9.4f} {curve_flags(c) or '-'}"
        )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay captured solar I-V curves on CH1 (software curve-following "
            "loop) against an MPPT charger, with CH2 emulating the battery."
        )
    )
    parser.add_argument(
        "--resource",
        default=DEFAULT_RESOURCE,
        help=f"VISA resource string for the SMU (default: {DEFAULT_RESOURCE})",
    )
    parser.add_argument(
        "--description",
        help=(
            "Free-form label that becomes part of the output CSV file name "
            "(default: current date/time stamp)"
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="Capture CSV from log_solar_sweep.py (columns sweep..volts,amps)",
    )
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help=(
            "Prepare and print all curves from the capture CSV, then exit "
            "without opening any VISA resource"
        ),
    )
    parser.add_argument(
        "--dwell",
        type=float,
        default=DEFAULT_DWELL_S,
        help=f"Seconds to play each curve (default: {DEFAULT_DWELL_S})",
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=None,
        help=(
            "Derive per-curve dwell from the capture's real elapsed time "
            "between sweeps divided by this factor (overrides --dwell)"
        ),
    )
    parser.add_argument(
        "--max-sweeps",
        type=int,
        default=None,
        help="Play at most this many sweeps (default: all)",
    )
    parser.add_argument(
        "--sweep-set",
        type=int,
        action="append",
        default=None,
        help=(
            "Play only these sweep indices (repeatable); indices are the 'idx' "
            "ordinals printed by --parse-only, i.e. file order"
        ),
    )
    parser.add_argument(
        "--include-dark",
        action="store_true",
        help=f"Also play near-dark sweeps (Pmp < {LOW_POWER_W * 1e3:.0f} mW), skipped by default",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_ALPHA,
        help=f"Relaxation factor for the V_set update (default: {DEFAULT_ALPHA})",
    )
    parser.add_argument(
        "--nplc",
        type=float,
        default=DEFAULT_NPLC,
        help=f"Integration time (NPLC) for both channels (default: {DEFAULT_NPLC})",
    )
    parser.add_argument(
        "--tick-interval",
        type=float,
        default=DEFAULT_TICK_INTERVAL,
        help=f"Sleep between control ticks in seconds (default: {DEFAULT_TICK_INTERVAL})",
    )
    parser.add_argument(
        "--vbat",
        type=float,
        default=DEFAULT_VBAT,
        help=f"CH2 battery emulation voltage (default: {DEFAULT_VBAT} V)",
    )
    parser.add_argument(
        "--ch2-pos-limit",
        type=float,
        default=DEFAULT_CH2_POS_LIMIT,
        help=(
            "CH2 positive current compliance: max current the battery emulator "
            f"may source into the DUT (default: {DEFAULT_CH2_POS_LIMIT} A)"
        ),
    )
    parser.add_argument(
        "--ch2-neg-limit",
        type=float,
        default=DEFAULT_CH2_NEG_LIMIT,
        help=(
            "CH2 negative current compliance: max charge current into the "
            "emulated battery. Use the value discovered by "
            f"b2900_charge_efficiency.py's spot-check (default: {DEFAULT_CH2_NEG_LIMIT} A)"
        ),
    )
    parser.add_argument(
        "--hcap",
        action="store_true",
        help="Enable high-capacitance mode on both channels (set while outputs are off)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save efficiency / first-sweep PNGs (matplotlib savefig, no GUI)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"Override VISA/SCPI timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS})",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> list[str]:
    """Return a list of fatal argument errors.

    Pure function: must be called (and must pass) BEFORE the instrument is
    opened, because constructing the driver issues *RST to the SMU.
    """
    errors: list[str] = []
    if args.vbat > VBAT_ABS_MAX:
        errors.append(
            f"REFUSED: --vbat={args.vbat} exceeds VBAT_ABS_MAX={VBAT_ABS_MAX} V "
            "(hard battery-safety ceiling; will not clamp)"
        )
    if args.vbat <= 0:
        errors.append("--vbat must be positive")
    if not args.csv.exists():
        errors.append(f"capture CSV not found: {args.csv}")
    if not 0 < args.alpha <= 1.0:
        errors.append("--alpha must be in (0, 1]")
    if args.dwell <= 0:
        errors.append("--dwell must be positive")
    if args.time_scale is not None and args.time_scale <= 0:
        errors.append("--time-scale must be positive")
    if args.max_sweeps is not None and args.max_sweeps < 1:
        errors.append("--max-sweeps must be >= 1")
    if args.sweep_set is not None and any(idx < 1 for idx in args.sweep_set):
        errors.append("--sweep-set indices must be >= 1")
    if args.nplc <= 0:
        errors.append("--nplc must be positive")
    if args.tick_interval < 0:
        errors.append("--tick-interval must be >= 0")
    if args.ch2_pos_limit <= 0:
        errors.append("--ch2-pos-limit must be positive")
    if not 0 < args.ch2_neg_limit <= 3.0:
        errors.append("--ch2-neg-limit must be in (0, 3.0] A")
    return errors


def sanitize_description(description: str | None) -> str:
    if not description:
        description = time.strftime("%Y%m%d_%H%M%S")
    cleaned = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in description
    ).strip("_")
    return cleaned or "session"


def pick_fixed_current_range(min_amps: float) -> float:
    """Smallest B2902B current range >= min_amps (caps at the largest range)."""
    for rng in CURRENT_RANGES:
        if rng >= min_amps:
            return rng
    return CURRENT_RANGES[-1]


def select_curves(curves: list, args: argparse.Namespace) -> list:
    selected = curves
    if args.sweep_set:
        wanted = set(args.sweep_set)
        selected = [c for c in selected if c.index in wanted]
        missing = wanted - {c.index for c in selected}
        if missing:
            print(f"WARNING: --sweep-set indices not found: {sorted(missing)}")
    if not args.include_dark:
        dark = [c.index for c in selected if c.low_power]
        if dark:
            print(
                f"Skipping {len(dark)} near-dark sweep(s) with Pmp < "
                f"{LOW_POWER_W * 1e3:.0f} mW (use --include-dark to keep): {dark}"
            )
        selected = [c for c in selected if not c.low_power]
    if args.max_sweeps is not None:
        selected = selected[: args.max_sweeps]
    return selected


def dwell_for(curve: SolarCurve, curves_by_index: dict, args: argparse.Namespace,
              previous_dwell: float) -> float:
    """Per-curve dwell: --dwell, or the capture's inter-sweep gap / --time-scale."""
    if args.time_scale is None:
        return args.dwell
    nxt = curves_by_index.get(curve.index + 1)
    if nxt is not None:
        delta = nxt.elapsed_s - curve.elapsed_s
        if delta > 0:
            return delta / args.time_scale
    return previous_dwell if previous_dwell > 0 else args.dwell


# --------------------------------------------------------------------------
# Instrument setup / playback (mirrors b2900_charge_efficiency.py patterns)
# --------------------------------------------------------------------------

def read_both(smu) -> tuple[float, float, float, float]:
    """One synchronized READ? of both channels -> (v1, i1, v2, i2)."""
    records = smu.read_measurements([1, 2], ["VOLT", "CURR"])
    r1, r2 = records
    return (
        r1.get("VOLT") or 0.0,
        r1.get("CURR") or 0.0,
        r2.get("VOLT") or 0.0,
        r2.get("CURR") or 0.0,
    )


def output_on_with_readback(smu, channel: int, set_volts: float) -> None:
    smu.driver.write(f":OUTP{channel} ON")
    time.sleep(0.2)
    record = smu.read_measurements([channel], ["VOLT", "CURR"])[0]
    measured = record.get("VOLT")
    if measured is None or abs(measured - set_volts) > READBACK_TOL_V:
        raise RuntimeError(
            f"ABORT: CH{channel} readback {measured} V deviates from setpoint "
            f"{set_volts} V by more than {READBACK_TOL_V * 1e3:.0f} mV "
            "(open sense lead / wiring fault?)"
        )
    print(f"CH{channel} on: readback {measured:.4f} V (set {set_volts:.4f} V) OK")


def check_error_queue(smu, context: str) -> None:
    codes = smu.system.read_error_codes()
    codes = [code for code in codes if code != 0]
    if codes:
        raise RuntimeError(f"ABORT: instrument error queue not clean {context}: {codes}")


def configure_channels(smu, args: argparse.Namespace, first_curve: SolarCurve) -> None:
    """Program both channels while outputs are off (constructor already *RST)."""
    driver = smu.driver
    driver.write("*CLS")
    smu.system.clear_error_queue()

    smu.output.set_output_off_mode("HIZ", channel=1)
    smu.output.set_output_off_mode("HIZ", channel=2)
    # After *RST, :OUTP<n>:ON:AUTO is ON: a READ?/MEAS? with the output off
    # would silently enable it. Disable so outputs turn on only explicitly.
    driver.write(":OUTP1:ON:AUTO OFF")
    driver.write(":OUTP2:ON:AUTO OFF")
    # :OUTP[c]:LOW resets to GROund (LOW terminal tied to chassis); two channels
    # on one DUT then share a ground through the instrument. Float both.
    driver.write(":OUTP1:LOW FLO")
    driver.write(":OUTP2:LOW FLO")
    if args.hcap:
        smu.output.set_high_capacitance_mode(True, channel=1)
        smu.output.set_high_capacitance_mode(True, channel=2)

    # CH2 = battery emulation (turns on first / off last).
    driver.write(":SOUR2:FUNC:MODE VOLT")
    driver.write(":SOUR2:VOLT:MODE FIX")
    driver.write(":SOUR2:VOLT:RANG 20")
    driver.write(f":SOUR2:VOLT:LEV {args.vbat}")
    driver.write(f":SOUR2:VOLT:TRIG {args.vbat}")  # READ?/:INIT must not move the source
    # One combined write: a second :FUNC replaces (not adds to) the list.
    driver.write(':SENS2:FUNC "VOLT","CURR"')
    smu.sense.enable_remote_sense(True, channel=2)
    driver.write(f":SENS2:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS2:CURR:NPLC {args.nplc}")
    driver.write(f":SENS2:CURR:PROT:POS {args.ch2_pos_limit}")
    driver.write(f":SENS2:CURR:PROT:NEG {args.ch2_neg_limit}")
    ch2_range = pick_fixed_current_range(1.2 * args.ch2_neg_limit)
    driver.write(":SENS2:CURR:RANG:AUTO OFF")
    driver.write(f":SENS2:CURR:RANG {ch2_range}")

    # CH1 = panel emulation; starts at the first curve's Voc.
    driver.write(":SOUR1:FUNC:MODE VOLT")
    driver.write(":SOUR1:VOLT:MODE FIX")
    driver.write(":SOUR1:VOLT:RANG 20")
    driver.write(f":SOUR1:VOLT:LEV {first_curve.voc}")
    driver.write(f":SOUR1:VOLT:TRIG {first_curve.voc}")  # READ?/:INIT must not move the source
    # One combined write: a second :FUNC replaces (not adds to) the list.
    driver.write(':SENS1:FUNC "VOLT","CURR"')
    smu.sense.enable_remote_sense(True, channel=1)
    driver.write(f":SENS1:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS1:CURR:NPLC {args.nplc}")
    driver.write(f":SENS1:CURR:PROT:NEG {DEFAULT_CH1_NEG_LIMIT}")
    apply_curve_protection(smu, first_curve)

    driver.write(":TRIG1:ACQ:COUN 1")
    driver.write(":TRIG2:ACQ:COUN 1")

    check_error_queue(smu, "after channel configuration")


def apply_curve_protection(smu, curve: SolarCurve) -> None:
    """Rectangle backstop for one sweep: PROT:POS = 1.1*Isc, fixed range."""
    i_lim = 1.1 * curve.isc
    rng = pick_fixed_current_range(1.2 * i_lim)
    smu.driver.write(":SENS1:CURR:RANG:AUTO OFF")
    smu.driver.write(f":SENS1:CURR:RANG {rng}")
    smu.driver.write(f":SENS1:CURR:PROT:POS {i_lim}")


def play_curve(smu, curve: SolarCurve, dwell_s: float, args: argparse.Namespace,
               run_start: float, writer, csv_file, collect_ticks: bool) -> dict:
    """Play one curve for dwell_s seconds; returns the per-sweep summary."""
    apply_curve_protection(smu, curve)
    v_set = curve.voc
    smu.driver.write(f":SOUR1:VOLT:LEV {v_set}")

    smu.driver.write(f":SOUR1:VOLT:TRIG {v_set}")  # READ?/:INIT must not move the source
    p_in_values: list = []
    p_out_values: list = []
    tick_points: list = []
    ticks = 0
    start = time.time()
    while time.time() - start < dwell_s:
        v1, i1, v2, i2 = read_both(smu)
        v_target = curve.v_panel(i1)
        v_set = v_set + args.alpha * (v_target - v_set)
        v_set = min(max(v_set, V_SET_FLOOR), curve.voc)
        smu.driver.write(f":SOUR1:VOLT:LEV {v_set}")
        smu.driver.write(f":SOUR1:VOLT:TRIG {v_set}")  # READ?/:INIT must not move the source
        ticks += 1
        p_in = v1 * i1
        p_out = v2 * -i2
        p_in_values.append(p_in)
        p_out_values.append(p_out)
        if collect_ticks:
            tick_points.append((v1, i1))
        writer.writerow([
            curve.index, ticks, f"{time.time() - run_start:.3f}",
            f"{v_set:.5f}", v1, i1, f"{p_in:.6f}", v2, i2, f"{p_out:.6f}",
        ])
        if args.tick_interval > 0:
            time.sleep(args.tick_interval)
    csv_file.flush()
    actual_dwell = time.time() - start
    tick_rate = ticks / actual_dwell if actual_dwell > 0 else 0.0

    p_in_mean = statistics.fmean(p_in_values) if p_in_values else 0.0
    p_out_mean = statistics.fmean(p_out_values) if p_out_values else 0.0
    p_in_std = statistics.pstdev(p_in_values) if len(p_in_values) > 1 else 0.0

    flags = []
    if p_in_mean > 0 and p_in_std / p_in_mean > UNSTABLE_RATIO:
        flags.append("UNSTABLE")
    if curve.low_power:
        flags.append("LOW_POWER")

    tracking_eff = p_in_mean / curve.pmp if curve.pmp > 0 else float("nan")
    converter_eff = p_out_mean / p_in_mean if p_in_mean > 0 else float("nan")
    total_eff = p_out_mean / curve.pmp if curve.pmp > 0 else float("nan")

    return {
        "sweep_idx": curve.index,
        "capture_timestamp": curve.timestamp,
        "dwell_s": round(actual_dwell, 3),
        "ticks": ticks,
        "tick_rate_hz": round(tick_rate, 2),
        "pmp_w": curve.pmp,
        "p_in_mean_w": p_in_mean,
        "p_out_mean_w": p_out_mean,
        "tracking_eff": tracking_eff,
        "converter_eff": converter_eff,
        "total_eff": total_eff,
        "flags": "|".join(flags),
        "_tick_points": tick_points,
    }


SUMMARY_COLUMNS = (
    "sweep_idx", "capture_timestamp", "dwell_s", "ticks", "tick_rate_hz",
    "pmp_w", "p_in_mean_w", "p_out_mean_w",
    "tracking_eff", "converter_eff", "total_eff", "flags",
)

TICK_COLUMNS = (
    "sweep_idx", "tick", "t_wall", "v_set", "v1", "i1", "p_in", "v2", "i2", "p_out",
)


def fmt_eff(value: float) -> str:
    return "   nan" if math.isnan(value) else f"{value * 100:6.1f}"


def print_summary(rows: list) -> None:
    header = (
        f"{'idx':>4} {'timestamp':<20} {'dwell':>6} {'ticks':>5} {'Hz':>5} "
        f"{'Pmp(W)':>8} {'Pin(W)':>8} {'Pout(W)':>8} "
        f"{'trk%':>6} {'conv%':>6} {'tot%':>6} flags"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['sweep_idx']:>4} {r['capture_timestamp']:<20} {r['dwell_s']:>6.1f} "
            f"{r['ticks']:>5} {r['tick_rate_hz']:>5.1f} {r['pmp_w']:>8.4f} "
            f"{r['p_in_mean_w']:>8.4f} {r['p_out_mean_w']:>8.4f} "
            f"{fmt_eff(r['tracking_eff'])} {fmt_eff(r['converter_eff'])} "
            f"{fmt_eff(r['total_eff'])} {r['flags'] or '-'}"
        )

    def overall(key: str) -> float:
        values = [r[key] for r in rows if not math.isnan(r[key])]
        return statistics.fmean(values) if values else float("nan")

    print(
        f"\nOverall means: tracking {fmt_eff(overall('tracking_eff')).strip()}%  "
        f"converter {fmt_eff(overall('converter_eff')).strip()}%  "
        f"total {fmt_eff(overall('total_eff')).strip()}%"
    )
    rates = [r["tick_rate_hz"] for r in rows]
    if rates and statistics.fmean(rates) < MIN_TICK_RATE_HZ:
        print(
            f"WARNING: mean achieved tick rate {statistics.fmean(rates):.1f} Hz < "
            f"{MIN_TICK_RATE_HZ:.0f} Hz -- control loop too slow to trust curve "
            "fidelity (lower --nplc / --tick-interval)"
        )


def save_plots(rows: list, first_curve: SolarCurve | None, base: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5))
    xs = [r["sweep_idx"] for r in rows]
    for key, label in (
        ("tracking_eff", "tracking"),
        ("converter_eff", "converter"),
        ("total_eff", "total"),
    ):
        ys = [r[key] * 100 if not math.isnan(r[key]) else float("nan") for r in rows]
        ax.plot(xs, ys, "o-", label=label)
    ax.set_xlabel("sweep index")
    ax.set_ylabel("Efficiency (%)")
    ax.set_title("Profile playback efficiency per sweep")
    ax.grid(True, alpha=0.3)
    ax.legend()
    path1 = base.with_name(base.name + "_eff.png")
    fig.savefig(path1, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot written to {path1}")

    if first_curve is None:
        return
    first_row = next((r for r in rows if r["sweep_idx"] == first_curve.index), None)
    if first_row is None or not first_row["_tick_points"]:
        return
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(
        [p[0] for p in first_curve.points],
        [p[1] for p in first_curve.points],
        "-", color="tab:gray", label=f"captured curve #{first_curve.index}",
    )
    ax.plot(
        [p[0] for p in first_row["_tick_points"]],
        [p[1] for p in first_row["_tick_points"]],
        ".", color="tab:red", label="measured operating points",
    )
    ax.plot(first_curve.vmp, first_curve.imp, "*", color="tab:green",
            markersize=12, label="Pmp")
    ax.set_xlabel("V (V)")
    ax.set_ylabel("I (A)")
    ax.set_title("First played sweep: curve vs operating points")
    ax.grid(True, alpha=0.3)
    ax.legend()
    path2 = base.with_name(base.name + "_first_sweep.png")
    fig.savefig(path2, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot written to {path2}")


def main() -> int:
    args = parse_args()

    # --- validation: MUST precede instrument connection (constructor *RSTs) ---
    errors = validate_args(args)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    curves = prepare_curves(args.csv)
    if not curves:
        print("No usable sweeps in the capture CSV.", file=sys.stderr)
        return 1
    print(f"Prepared {len(curves)} curve(s) from {args.csv}:\n")
    print_curve_table(curves)

    if args.parse_only:
        # Offline verification path: exits before any VISA/instrument object
        # is constructed.
        return 0

    selected = select_curves(curves, args)
    if not selected:
        print("No sweeps selected for playback.", file=sys.stderr)
        return 1
    curves_by_index = {c.index: c for c in curves}

    # Import here so the --parse-only path never touches pyvisa-backed modules.
    from testbench.core.scpi import SCPISettings
    from testbench.sourcemeter.keysight_b2902b import KeysightB2902B

    description = sanitize_description(args.description)
    base = Path("data") / f"profile_playback_{description}"
    base.parent.mkdir(parents=True, exist_ok=True)
    ticks_path = base.with_name(base.name + ".csv")
    summary_path = base.with_name(base.name + "_summary.csv")

    settings = SCPISettings(timeout_ms=args.timeout_ms)
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        print("B2902B not found")
        return 1

    summary_rows: list = []
    exit_code = 0
    ticks_file = ticks_path.open("w", newline="")
    ticks_writer = csv.writer(ticks_file)
    ticks_writer.writerow(TICK_COLUMNS)
    try:
        configure_channels(smu, args, selected[0])

        # Battery on first, panel second, each with an open-sense readback check.
        output_on_with_readback(smu, 2, args.vbat)
        output_on_with_readback(smu, 1, selected[0].voc)

        run_start = time.time()
        previous_dwell = 0.0
        for position, curve in enumerate(selected):
            dwell_s = dwell_for(curve, curves_by_index, args, previous_dwell)
            previous_dwell = dwell_s
            print(
                f"\nPlaying sweep #{curve.index} ({curve.timestamp}, "
                f"Pmp {curve.pmp:.3f} W) for {dwell_s:.1f} s..."
            )
            row = play_curve(
                smu, curve, dwell_s, args, run_start,
                ticks_writer, ticks_file, collect_ticks=(position == 0),
            )
            summary_rows.append(row)
            print(
                f"  ticks={row['ticks']} ({row['tick_rate_hz']:.1f} Hz) "
                f"Pin={row['p_in_mean_w']:.4f} W Pout={row['p_out_mean_w']:.4f} W "
                f"trk={fmt_eff(row['tracking_eff']).strip()}% "
                f"conv={fmt_eff(row['converter_eff']).strip()}% "
                f"tot={fmt_eff(row['total_eff']).strip()}% "
                f"flags={row['flags'] or '-'}"
            )
    except (RuntimeError, KeyboardInterrupt) as exc:
        print(f"\nRun stopped: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        # Teardown: CH1 (panel) off first, CH2 (battery) off last, then errors.
        try:
            smu.driver.write(":OUTP1 OFF")
        except Exception as exc:  # noqa: BLE001 - best-effort teardown
            print(f"teardown: OUTP1 OFF failed: {exc}", file=sys.stderr)
        try:
            smu.driver.write(":OUTP2 OFF")
        except Exception as exc:  # noqa: BLE001
            print(f"teardown: OUTP2 OFF failed: {exc}", file=sys.stderr)
        try:
            codes = [code for code in smu.system.read_error_codes() if code != 0]
            print(f"instrument error queue at exit: {codes if codes else 'clean'}")
        except Exception as exc:  # noqa: BLE001
            print(f"teardown: error-queue read failed: {exc}", file=sys.stderr)
        try:
            ticks_file.close()
        except Exception:  # noqa: BLE001
            pass

    if summary_rows:
        with summary_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\nTick log written to {ticks_path}")
        print(f"Summary written to {summary_path}\n")
        print_summary(summary_rows)
        if args.plot:
            save_plots(summary_rows, selected[0] if selected else None, base)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
