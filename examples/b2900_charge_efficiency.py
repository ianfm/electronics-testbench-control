"""Solar MPPT charger efficiency grid: eta = P_battery / P_solar on a B2902B.

CH1 emulates the solar panel (CV source at Voc or Vmp with the positive
current compliance acting as the irradiance knob).  CH2 emulates the LiFePO4
battery (CV source, 3.0-3.5 V).  Both channels are sampled synchronously with
``READ? (@1,2)`` and per-point efficiency is computed as the ratio of mean
powers.

Safety invariants (hard-coded):
  * Refuses any battery voltage argument above ``VBAT_ABS_MAX`` (3.55 V).
  * Output-off mode HIZ on both channels before any output-on.
  * Asymmetric compliance: CH1 PROT:NEG small (never drives the DUT input),
    CH2 PROT:POS small (never sources into the charger output).
  * CH2 (battery) on first / off last; CH1 (solar) on second / off first.
  * After each output-on the source voltage is read back and the run aborts
    if it deviates from the setpoint by more than 50 mV (open sense lead).

All argument validation happens before the instrument is opened (the driver
constructor issues *RST on connect).
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from testbench.core.scpi import SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B

DEFAULT_RESOURCE = "USB0::10893::37377::MY60440156::0::INSTR"

VBAT_ABS_MAX = 3.55  # hard refusal ceiling for any battery-side setpoint
READBACK_TOL_V = 0.050  # abort threshold after output-on (open-sense detector)
CHARGER_OFF_W = 0.005  # below this mean output power the charger is "off"
CH2_NEG_LIMIT_FLOOR = 0.1
CH2_NEG_LIMIT_CAP = 1.0
DISCOVERY_VBAT = 3.2
DISCOVERY_POLL_S = 0.5  # 2 Hz
SETTLE_WINDOW = 4  # consecutive P_in readings used for convergence detection

# B2902B DC current measurement ranges (A).
CURRENT_RANGES = (1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 3.0)

DEFAULT_VOC = 8.0
DEFAULT_VMP = 7.2
DEFAULT_IMP = 0.35
DEFAULT_IRRADIANCE = "0.1,0.25,0.5,0.75,1.0"
DEFAULT_VBAT_START = 3.0
DEFAULT_VBAT_STOP = 3.5
DEFAULT_VBAT_STEPS = 6
DEFAULT_SAMPLES = 10
DEFAULT_SAMPLE_INTERVAL = 0.15
DEFAULT_NPLC = 1.0
DEFAULT_SETTLE_MIN = 2.0
DEFAULT_SETTLE_TIMEOUT = 20.0
DEFAULT_SETTLE_TOL = 0.02
DEFAULT_CH2_POS_LIMIT = 0.05
DEFAULT_CH1_NEG_LIMIT = 0.01
DEFAULT_UNSTABLE_THRESHOLD = 0.05
DEFAULT_TIMEOUT_MS = 30000

MEASURE_ELEMENTS = ("VOLT", "CURR", "TIME", "STAT")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure solar MPPT charger efficiency over an irradiance x Vbat "
            "grid with the B2902B emulating both panel (CH1) and battery (CH2)."
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
            "Free-form label that becomes part of the CSV file name "
            "(default: current date/time stamp)"
        ),
    )
    parser.add_argument(
        "--voc",
        type=float,
        default=DEFAULT_VOC,
        help=f"Panel open-circuit voltage from pre-characterization (default: {DEFAULT_VOC} V)",
    )
    parser.add_argument(
        "--vmp",
        type=float,
        default=DEFAULT_VMP,
        help=f"Panel maximum-power voltage from pre-characterization (default: {DEFAULT_VMP} V)",
    )
    parser.add_argument(
        "--imp",
        type=float,
        default=DEFAULT_IMP,
        help=f"Panel maximum-power current from pre-characterization (default: {DEFAULT_IMP} A)",
    )
    parser.add_argument(
        "--irradiance",
        default=DEFAULT_IRRADIANCE,
        help=(
            "Comma-separated irradiance fractions; each scales Imp to set the "
            f"CH1 positive current limit (default: {DEFAULT_IRRADIANCE})"
        ),
    )
    parser.add_argument(
        "--vsol-mode",
        choices=("voc", "vmp"),
        default="voc",
        help=(
            "CH1 source voltage: 'voc' (right for fractional-Voc trackers) or "
            "'vmp' (default: voc)"
        ),
    )
    parser.add_argument(
        "--vbat-start",
        type=float,
        default=DEFAULT_VBAT_START,
        help=f"Lowest battery emulation voltage (default: {DEFAULT_VBAT_START} V)",
    )
    parser.add_argument(
        "--vbat-stop",
        type=float,
        default=DEFAULT_VBAT_STOP,
        help=f"Highest battery emulation voltage (default: {DEFAULT_VBAT_STOP} V)",
    )
    parser.add_argument(
        "--vbat-steps",
        type=int,
        default=DEFAULT_VBAT_STEPS,
        help=f"Number of battery voltage points (default: {DEFAULT_VBAT_STEPS})",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help=f"Synchronized samples per grid point (default: {DEFAULT_SAMPLES})",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=DEFAULT_SAMPLE_INTERVAL,
        help=f"Seconds between samples at a grid point (default: {DEFAULT_SAMPLE_INTERVAL})",
    )
    parser.add_argument(
        "--nplc",
        type=float,
        default=DEFAULT_NPLC,
        help=f"Integration time (NPLC) for both channels (default: {DEFAULT_NPLC})",
    )
    parser.add_argument(
        "--settle-min",
        type=float,
        default=DEFAULT_SETTLE_MIN,
        help=f"Minimum settle seconds per grid point (default: {DEFAULT_SETTLE_MIN})",
    )
    parser.add_argument(
        "--settle-timeout",
        type=float,
        default=DEFAULT_SETTLE_TIMEOUT,
        help=f"Maximum settle seconds per grid point (default: {DEFAULT_SETTLE_TIMEOUT})",
    )
    parser.add_argument(
        "--settle-tol",
        type=float,
        default=DEFAULT_SETTLE_TOL,
        help=(
            "Relative P_in window spread below which the point is considered "
            f"settled (default: {DEFAULT_SETTLE_TOL})"
        ),
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
        default=None,
        help=(
            "CH2 negative current compliance: max charge current into the "
            "emulated battery (default: discovered by the spot-check)"
        ),
    )
    parser.add_argument(
        "--ch1-neg-limit",
        type=float,
        default=DEFAULT_CH1_NEG_LIMIT,
        help=(
            "CH1 negative current compliance: backdrive protection for the "
            f"panel emulator (default: {DEFAULT_CH1_NEG_LIMIT} A)"
        ),
    )
    parser.add_argument(
        "--unstable-threshold",
        type=float,
        default=DEFAULT_UNSTABLE_THRESHOLD,
        help=(
            "Flag a point UNSTABLE when std/mean of P_in exceeds this "
            f"(default: {DEFAULT_UNSTABLE_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--hcap",
        action="store_true",
        help="Enable high-capacitance mode on both channels (set while outputs are off)",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip the discovery spot-check (requires an explicit --ch2-neg-limit)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save efficiency PNGs next to the CSVs (matplotlib savefig, no GUI)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"Override VISA/SCPI timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS})",
    )
    return parser.parse_args(argv)


def parse_irradiance(text: str) -> list[float]:
    """Parse the comma-separated irradiance fraction list (pure, testable)."""
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    return values


def validate_args(args: argparse.Namespace) -> list[str]:
    """Return a list of fatal argument errors.

    Pure function: must be called (and must pass) BEFORE the instrument is
    opened, because constructing the driver issues *RST to the SMU.
    """
    errors: list[str] = []

    for name, value in (("--vbat-start", args.vbat_start), ("--vbat-stop", args.vbat_stop)):
        if value > VBAT_ABS_MAX:
            errors.append(
                f"REFUSED: {name}={value} exceeds VBAT_ABS_MAX={VBAT_ABS_MAX} V "
                "(hard battery-safety ceiling; will not clamp)"
            )
        if value <= 0:
            errors.append(f"{name} must be positive (got {value})")
    if args.vbat_stop < args.vbat_start:
        errors.append("--vbat-stop must be >= --vbat-start")
    if args.vbat_steps < 1:
        errors.append("--vbat-steps must be >= 1")

    if args.skip_discovery and args.ch2_neg_limit is None:
        errors.append(
            "REFUSED: --skip-discovery requires an explicit --ch2-neg-limit "
            "(the discovery spot-check is what normally sets the charge-current limit)"
        )
    if args.ch2_neg_limit is not None and not 0 < args.ch2_neg_limit <= 3.0:
        errors.append("--ch2-neg-limit must be in (0, 3.0] A")
    if args.ch2_pos_limit <= 0:
        errors.append("--ch2-pos-limit must be positive")
    if args.ch1_neg_limit <= 0:
        errors.append("--ch1-neg-limit must be positive")

    if args.voc <= 0:
        errors.append("--voc must be positive")
    if not 0 < args.vmp <= args.voc:
        errors.append("--vmp must be positive and <= --voc")
    if args.imp <= 0:
        errors.append("--imp must be positive")

    try:
        fractions = parse_irradiance(args.irradiance)
    except ValueError:
        fractions = []
        errors.append(f"--irradiance is not a comma-separated float list: {args.irradiance!r}")
    if not fractions:
        errors.append("--irradiance must contain at least one fraction")
    for f in fractions:
        if not 0 < f <= 1.5:
            errors.append(f"irradiance fraction {f} out of range (0, 1.5]")

    if args.samples < 1:
        errors.append("--samples must be >= 1")
    if args.sample_interval < 0:
        errors.append("--sample-interval must be >= 0")
    if args.nplc <= 0:
        errors.append("--nplc must be positive")
    if args.settle_min < 0:
        errors.append("--settle-min must be >= 0")
    if args.settle_timeout < args.settle_min:
        errors.append("--settle-timeout must be >= --settle-min")
    if args.settle_tol <= 0:
        errors.append("--settle-tol must be positive")
    if args.unstable_threshold <= 0:
        errors.append("--unstable-threshold must be positive")
    return errors


def pick_fixed_current_range(min_amps: float) -> float:
    """Smallest B2902B current range >= min_amps (caps at the largest range)."""
    for rng in CURRENT_RANGES:
        if rng >= min_amps:
            return rng
    return CURRENT_RANGES[-1]


def sanitize_description(description: str | None) -> str:
    if not description:
        description = time.strftime("%Y%m%d_%H%M%S")
    cleaned = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in description
    ).strip("_")
    return cleaned or "session"


def linear_space(start: float, stop: float, steps: int):
    if steps <= 1:
        yield stop
        return
    delta = (stop - start) / float(steps - 1)
    for idx in range(steps):
        yield start + idx * delta


def read_both(smu: KeysightB2902B) -> tuple[dict, dict]:
    """One synchronized READ? of both channels; returns (ch1, ch2) dicts."""
    records = smu.read_measurements([1, 2], MEASURE_ELEMENTS)
    out = []
    for record in records:
        out.append(
            {
                "volt": record.get("VOLT"),
                "curr": record.get("CURR"),
                "time": record.get("TIME"),
                "stat": record.get("STAT"),
            }
        )
    return out[0], out[1]


def output_on_with_readback(smu: KeysightB2902B, channel: int, set_volts: float) -> None:
    """Enable one output, then abort if the readback deviates from the setpoint.

    Detects an open sense lead before remote-sense runaway can push the DUT
    past its limits.
    """
    smu.driver.write(f":OUTP{channel} ON")
    time.sleep(0.2)
    record = smu.read_measurements([channel], MEASURE_ELEMENTS)[0]
    measured = record.get("VOLT")
    if measured is None or abs(measured - set_volts) > READBACK_TOL_V:
        raise RuntimeError(
            f"ABORT: CH{channel} readback {measured} V deviates from setpoint "
            f"{set_volts} V by more than {READBACK_TOL_V * 1e3:.0f} mV "
            "(open sense lead / wiring fault?)"
        )
    print(f"CH{channel} on: readback {measured:.4f} V (set {set_volts:.4f} V) OK")


def check_error_queue(smu: KeysightB2902B, context: str) -> None:
    codes = smu.system.read_error_codes()
    codes = [code for code in codes if code != 0]
    if codes:
        raise RuntimeError(f"ABORT: instrument error queue not clean {context}: {codes}")


def configure_channels(smu: KeysightB2902B, args: argparse.Namespace, vsol: float,
                       ch2_neg_limit: float) -> None:
    """Program both channels while outputs are off (constructor already *RST)."""
    driver = smu.driver
    driver.write("*CLS")
    smu.system.clear_error_queue()

    # Output-off mode HIZ + optional high-capacitance mode, outputs off.
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

    # CH2 = battery emulation (configured first; it turns on first).
    driver.write(":SOUR2:FUNC:MODE VOLT")
    driver.write(":SOUR2:VOLT:MODE FIX")
    driver.write(":SOUR2:VOLT:RANG 20")
    driver.write(f":SOUR2:VOLT:LEV {args.vbat_start}")
    driver.write(f":SOUR2:VOLT:TRIG {args.vbat_start}")  # READ?/:INIT must not move the source
    # One combined write: a second :FUNC replaces (not adds to) the list.
    driver.write(':SENS2:FUNC "VOLT","CURR"')
    smu.sense.enable_remote_sense(True, channel=2)
    driver.write(f":SENS2:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS2:CURR:NPLC {args.nplc}")
    driver.write(f":SENS2:CURR:PROT:POS {args.ch2_pos_limit}")
    driver.write(f":SENS2:CURR:PROT:NEG {ch2_neg_limit}")

    # CH1 = panel emulation.
    driver.write(":SOUR1:FUNC:MODE VOLT")
    driver.write(":SOUR1:VOLT:MODE FIX")
    driver.write(":SOUR1:VOLT:RANG 20")
    driver.write(f":SOUR1:VOLT:LEV {vsol}")
    driver.write(f":SOUR1:VOLT:TRIG {vsol}")  # READ?/:INIT must not move the source
    # One combined write: a second :FUNC replaces (not adds to) the list.
    driver.write(':SENS1:FUNC "VOLT","CURR"')
    smu.sense.enable_remote_sense(True, channel=1)
    driver.write(f":SENS1:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS1:CURR:NPLC {args.nplc}")
    driver.write(f":SENS1:CURR:PROT:POS {args.imp}")
    driver.write(f":SENS1:CURR:PROT:NEG {args.ch1_neg_limit}")

    driver.write(":TRIG1:ACQ:COUN 1")
    driver.write(":TRIG2:ACQ:COUN 1")

    check_error_queue(smu, "after channel configuration")


def set_ch1_irradiance(smu: KeysightB2902B, i_lim: float) -> None:
    """Set the CH1 compliance (irradiance knob) and a fixed measure range."""
    rng = pick_fixed_current_range(1.2 * i_lim)
    smu.driver.write(":SENS1:CURR:RANG:AUTO OFF")
    smu.driver.write(f":SENS1:CURR:RANG {rng}")
    smu.driver.write(f":SENS1:CURR:PROT:POS {i_lim}")


def run_discovery(smu: KeysightB2902B, args: argparse.Namespace) -> dict:
    """Spot-check at Vbat=3.2 V, I_lim=Imp to learn charge current and settling.

    Returns {'ch2_neg_limit', 'settle_floor', 'cold_start', 'quiescent_ok'}.
    """
    print("--- discovery spot-check (Vbat %.2f V, I_lim %.3f A) ---" % (DISCOVERY_VBAT, args.imp))
    driver = smu.driver
    driver.write(f":SOUR2:VOLT:LEV {DISCOVERY_VBAT}")
    driver.write(f":SOUR2:VOLT:TRIG {DISCOVERY_VBAT}")  # READ?/:INIT must not move the source
    set_ch1_irradiance(smu, args.imp)

    max_charge_a = 0.0
    settle_floor = args.settle_timeout
    window: list[float] = []
    settled_at = None
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > args.settle_timeout:
            break
        ch1, ch2 = read_both(smu)
        charge_a = -(ch2["curr"] or 0.0)
        max_charge_a = max(max_charge_a, charge_a)
        p_in = (ch1["volt"] or 0.0) * (ch1["curr"] or 0.0)
        window.append(p_in)
        if len(window) > SETTLE_WINDOW:
            window.pop(0)
        if settled_at is None and len(window) == SETTLE_WINDOW:
            mean = sum(window) / len(window)
            if mean > 0 and (max(window) - min(window)) / mean < args.settle_tol:
                settled_at = elapsed
                settle_floor = elapsed
        # Keep observing a little past the settle point to catch inrush peaks.
        if settled_at is not None and elapsed > settled_at + 2.0:
            break
        time.sleep(DISCOVERY_POLL_S)

    ch2_neg_limit = min(CH2_NEG_LIMIT_CAP, max(CH2_NEG_LIMIT_FLOOR, 1.5 * max_charge_a))
    print(f"max charge current observed: {max_charge_a:.4f} A -> CH2 PROT:NEG {ch2_neg_limit:.3f} A")
    if settled_at is None:
        print(f"WARNING: P_in did not settle within {args.settle_timeout:.1f} s")
        settle_floor = args.settle_min
    else:
        print(f"observed settle time: {settled_at:.1f} s (per-point settle floor)")

    # Cold-start check at the bottom of the battery range.
    driver.write(":SOUR2:VOLT:LEV 3.0")
    driver.write(":SOUR2:VOLT:TRIG 3.0")  # READ?/:INIT must not move the source
    time.sleep(max(args.settle_min, settle_floor))
    ch1, ch2 = read_both(smu)
    p_out = (ch2["volt"] or 0.0) * -(ch2["curr"] or 0.0)
    cold_start = p_out > CHARGER_OFF_W
    print(f"charger cold-start at Vbat 3.0 V: {'yes' if cold_start else 'NO'} "
          f"(P_out {p_out * 1e3:.1f} mW)")

    # Quiescent battery-side draw must stay under the CH2 positive limit.
    quiescent_ok = not smu.sense.compliance_tripped("CURR", channel=2)
    if not quiescent_ok:
        print(f"WARNING: CH2 compliance tripped during discovery "
              f"(quiescent Vbat draw may exceed {args.ch2_pos_limit} A pos limit)")

    driver.write(f":SOUR2:VOLT:LEV {args.vbat_start}")

    driver.write(f":SOUR2:VOLT:TRIG {args.vbat_start}")  # READ?/:INIT must not move the source
    return {
        "ch2_neg_limit": ch2_neg_limit,
        "settle_floor": settle_floor,
        "cold_start": cold_start,
        "quiescent_ok": quiescent_ok,
    }


def settle_point(smu: KeysightB2902B, args: argparse.Namespace,
                 settle_floor: float) -> tuple[float, bool]:
    """Wait for P_in convergence; returns (settle_seconds, settled)."""
    window: list[float] = []
    start = time.time()
    min_wait = max(args.settle_min, settle_floor)
    while True:
        elapsed = time.time() - start
        ch1, _ = read_both(smu)
        p_in = (ch1["volt"] or 0.0) * (ch1["curr"] or 0.0)
        window.append(p_in)
        if len(window) > SETTLE_WINDOW:
            window.pop(0)
        if elapsed >= min_wait and len(window) == SETTLE_WINDOW:
            mean = sum(window) / len(window)
            spread = max(window) - min(window)
            if mean > 0 and spread / mean < args.settle_tol:
                return elapsed, True
            if mean <= 0 and spread < 1e-4:
                # Charger drawing nothing (e.g. terminated) is also "settled".
                return elapsed, True
        if elapsed >= args.settle_timeout:
            return elapsed, False
        time.sleep(0.25)


def measure_point(smu: KeysightB2902B, args: argparse.Namespace) -> list[dict]:
    samples = []
    for index in range(args.samples):
        ch1, ch2 = read_both(smu)
        samples.append(
            {
                "sample": index + 1,
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "ch1": ch1,
                "ch2": ch2,
                "p_in": (ch1["volt"] or 0.0) * (ch1["curr"] or 0.0),
                "p_out": (ch2["volt"] or 0.0) * -(ch2["curr"] or 0.0),
            }
        )
        if index + 1 < args.samples:
            time.sleep(args.sample_interval)
    return samples


def check_time_sync(samples: list[dict], nplc: float) -> None:
    """First-hardware-session check: READ? (@1,2) TIME elements should match."""
    first = samples[0]
    t1, t2 = first["ch1"]["time"], first["ch2"]["time"]
    if t1 is None or t2 is None:
        return
    aperture = nplc / 50.0
    delta = abs(t1 - t2)
    if delta > aperture:
        print(
            f"WARNING: channel TIME skew {delta * 1e3:.2f} ms exceeds one aperture "
            f"({aperture * 1e3:.2f} ms); READ? (@1,2) may not be simultaneous on "
            "this firmware. Consider :INIT (@1,2) + :FETC? (@1,2)."
        )
    else:
        print(f"channel TIME skew at first point: {delta * 1e3:.3f} ms (<= 1 aperture, OK)")


def summarize_point(f: float, i_lim: float, vsol: float, vbat: float,
                    settle_s: float, settled: bool, samples: list[dict],
                    ch1_trip: bool, ch2_trip: bool, unstable_threshold: float) -> dict:
    p_in = [s["p_in"] for s in samples]
    p_out = [s["p_out"] for s in samples]
    p_in_mean = statistics.fmean(p_in)
    p_out_mean = statistics.fmean(p_out)
    p_in_std = statistics.pstdev(p_in) if len(p_in) > 1 else 0.0
    p_out_std = statistics.pstdev(p_out) if len(p_out) > 1 else 0.0
    eta = (sum(p_out) / sum(p_in)) if sum(p_in) > 0 else float("nan")

    # Per-sample ratio spread -> standard error of the headline number.
    eta_err = float("nan")
    ratios = [s["p_out"] / s["p_in"] for s in samples if s["p_in"] > 0]
    if len(ratios) > 1:
        eta_err = statistics.pstdev(ratios) / math.sqrt(len(ratios))

    flags = []
    if not settled:
        flags.append("NOT_SETTLED")
    if p_in_mean > 0 and p_in_std / p_in_mean > unstable_threshold:
        flags.append("UNSTABLE")
    if ch1_trip:
        flags.append("CH1_COMPLIANCE")
    if p_out_mean < CHARGER_OFF_W:
        flags.append("CHARGER_OFF")

    return {
        "irradiance": f,
        "i_lim_a": i_lim,
        "vsol_set": vsol,
        "vbat_set": vbat,
        "settle_s": settle_s,
        "settled": settled,
        "samples": len(samples),
        "v1_mean": statistics.fmean([s["ch1"]["volt"] or 0.0 for s in samples]),
        "i1_mean": statistics.fmean([s["ch1"]["curr"] or 0.0 for s in samples]),
        "v2_mean": statistics.fmean([s["ch2"]["volt"] or 0.0 for s in samples]),
        "i2_mean": statistics.fmean([s["ch2"]["curr"] or 0.0 for s in samples]),
        "p_in_mean_w": p_in_mean,
        "p_in_std_w": p_in_std,
        "p_out_mean_w": p_out_mean,
        "p_out_std_w": p_out_std,
        "eta": eta,
        "eta_err": eta_err,
        "ch1_curr_trip": ch1_trip,
        "ch2_curr_trip": ch2_trip,
        "flags": "|".join(flags),
    }


SUMMARY_COLUMNS = (
    "irradiance", "i_lim_a", "vsol_set", "vbat_set", "settle_s", "settled",
    "samples", "v1_mean", "i1_mean", "v2_mean", "i2_mean",
    "p_in_mean_w", "p_in_std_w", "p_out_mean_w", "p_out_std_w",
    "eta", "eta_err", "ch1_curr_trip", "ch2_curr_trip", "flags",
)

SAMPLE_COLUMNS = (
    "irradiance", "i_lim_a", "vsol_set", "vbat_set", "sample", "timestamp",
    "t1", "v1", "i1", "stat1", "t2", "v2", "i2", "stat2", "p_in_w", "p_out_w",
)


def print_table(rows: list[dict]) -> None:
    fractions = sorted({row["irradiance"] for row in rows})
    vbats = sorted({row["vbat_set"] for row in rows})
    header = "f\\Vbat |" + "".join(f" {v:7.3f}" for v in vbats)
    print(header)
    print("-" * len(header))
    for f in fractions:
        cells = []
        for v in vbats:
            match = [r for r in rows if r["irradiance"] == f and r["vbat_set"] == v]
            if match:
                row = match[0]
                mark = "*" if row["flags"] else " "
                if math.isnan(row["eta"]):
                    cells.append("    nan ")
                else:
                    cells.append(f" {row['eta'] * 100:6.2f}{mark}")
            else:
                cells.append("      - ")
        print(f"{f:6.2f} |" + "".join(cells))
    if any(row["flags"] for row in rows):
        print("(* = flagged point; see summary CSV 'flags' column)")


def print_headline(rows: list[dict]) -> None:
    candidates = [r for r in rows if not r["flags"] and not math.isnan(r["eta"])]
    note = ""
    if not candidates:
        candidates = [r for r in rows if not math.isnan(r["eta"])]
        note = " (all points flagged; nearest flagged point shown)"
    if not candidates:
        print("No valid efficiency points measured.")
        return
    best = min(candidates, key=lambda r: (abs(r["irradiance"] - 1.0), abs(r["vbat_set"] - 3.3)))
    err = best["eta_err"] * 100 if not math.isnan(best["eta_err"]) else 0.0
    print(
        f"\nHEADLINE: eta = {best['eta'] * 100:.1f}% +/- {err:.1f}% at "
        f"Vbat={best['vbat_set']:.2f} V, irradiance={best['irradiance']:.2f} "
        f"(P_in {best['p_in_mean_w']:.3f} W){note}"
    )


def save_plots(rows: list[dict], base: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    valid = [r for r in rows if not math.isnan(r["eta"])]
    if not valid:
        print("No valid points to plot.")
        return

    # eta vs Vbat, one line per irradiance level; flagged points open markers.
    fig, ax = plt.subplots(figsize=(7, 5))
    for f in sorted({r["irradiance"] for r in valid}):
        series = sorted((r for r in valid if r["irradiance"] == f),
                        key=lambda r: r["vbat_set"])
        xs = [r["vbat_set"] for r in series]
        ys = [r["eta"] * 100 for r in series]
        (line,) = ax.plot(xs, ys, "-", label=f"f={f:.2f}")
        color = line.get_color()
        for r in series:
            if r["flags"]:
                ax.plot(r["vbat_set"], r["eta"] * 100, "o", mfc="none", mec=color)
            else:
                ax.plot(r["vbat_set"], r["eta"] * 100, "o", color=color)
    ax.set_xlabel("Vbat (V)")
    ax.set_ylabel("Efficiency (%)")
    ax.set_title("Charger efficiency vs Vbat (open markers = flagged)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    path1 = base.with_name(base.name + "_eta_vs_vbat.png")
    fig.savefig(path1, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # eta vs P_in.
    fig, ax = plt.subplots(figsize=(7, 5))
    for r in valid:
        style = {"mfc": "none"} if r["flags"] else {}
        ax.plot(r["p_in_mean_w"], r["eta"] * 100, "o", color="tab:blue", **style)
    ax.set_xlabel("P_in (W)")
    ax.set_ylabel("Efficiency (%)")
    ax.set_title("Charger efficiency vs input power (open markers = flagged)")
    ax.grid(True, alpha=0.3)
    path2 = base.with_name(base.name + "_eta_vs_pin.png")
    fig.savefig(path2, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plots written to {path1} and {path2}")


def main() -> int:
    args = parse_args()

    # --- validation: MUST precede instrument connection (constructor *RSTs) ---
    errors = validate_args(args)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    fractions = parse_irradiance(args.irradiance)
    vsol = args.voc if args.vsol_mode == "voc" else args.vmp
    description = sanitize_description(args.description)
    base = Path("data") / f"charge_eff_{description}"
    base.parent.mkdir(parents=True, exist_ok=True)
    samples_path = base.with_name(base.name + ".csv")
    summary_path = base.with_name(base.name + "_summary.csv")

    settings = SCPISettings(timeout_ms=args.timeout_ms)
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        print("B2902B not found")
        return 1

    summary_rows: list[dict] = []
    exit_code = 0
    samples_file = samples_path.open("w", newline="")
    samples_writer = csv.writer(samples_file)
    samples_writer.writerow(SAMPLE_COLUMNS)
    try:
        # Provisional CH2 charge limit until discovery refines it.
        initial_neg_limit = (
            args.ch2_neg_limit if args.ch2_neg_limit is not None else CH2_NEG_LIMIT_CAP
        )
        configure_channels(smu, args, vsol, initial_neg_limit)

        # Battery on first, panel second, each with an open-sense readback check.
        output_on_with_readback(smu, 2, args.vbat_start)
        output_on_with_readback(smu, 1, vsol)

        settle_floor = 0.0
        if args.skip_discovery:
            ch2_neg_limit = args.ch2_neg_limit
            print(f"discovery skipped; CH2 PROT:NEG = {ch2_neg_limit} A (from --ch2-neg-limit)")
        else:
            discovery = run_discovery(smu, args)
            ch2_neg_limit = (
                args.ch2_neg_limit if args.ch2_neg_limit is not None
                else discovery["ch2_neg_limit"]
            )
            settle_floor = discovery["settle_floor"]
        smu.driver.write(f":SENS2:CURR:PROT:NEG {ch2_neg_limit}")
        ch2_range = pick_fixed_current_range(1.2 * ch2_neg_limit)
        smu.driver.write(":SENS2:CURR:RANG:AUTO OFF")
        smu.driver.write(f":SENS2:CURR:RANG {ch2_range}")

        first_point = True
        for f in fractions:
            i_lim = f * args.imp
            set_ch1_irradiance(smu, i_lim)
            for vbat in linear_space(args.vbat_start, args.vbat_stop, args.vbat_steps):
                smu.driver.write(f":SOUR2:VOLT:LEV {vbat}")
                smu.driver.write(f":SOUR2:VOLT:TRIG {vbat}")  # READ?/:INIT must not move the source
                settle_s, settled = settle_point(smu, args, settle_floor)
                samples = measure_point(smu, args)
                if first_point:
                    check_time_sync(samples, args.nplc)
                    first_point = False
                ch1_trip = smu.sense.compliance_tripped("CURR", channel=1)
                ch2_trip = smu.sense.compliance_tripped("CURR", channel=2)
                row = summarize_point(
                    f, i_lim, vsol, vbat, settle_s, settled, samples,
                    ch1_trip, ch2_trip, args.unstable_threshold,
                )
                summary_rows.append(row)
                for s in samples:
                    samples_writer.writerow([
                        f, i_lim, vsol, vbat, s["sample"], s["timestamp"],
                        s["ch1"]["time"], s["ch1"]["volt"], s["ch1"]["curr"], s["ch1"]["stat"],
                        s["ch2"]["time"], s["ch2"]["volt"], s["ch2"]["curr"], s["ch2"]["stat"],
                        s["p_in"], s["p_out"],
                    ])
                samples_file.flush()
                eta_text = "nan" if math.isnan(row["eta"]) else f"{row['eta'] * 100:.2f}%"
                print(
                    f"f={f:.2f} Vbat={vbat:.3f} V: eta={eta_text} "
                    f"P_in={row['p_in_mean_w']:.4f} W P_out={row['p_out_mean_w']:.4f} W "
                    f"settle={settle_s:.1f}s flags={row['flags'] or '-'}"
                )
    except (RuntimeError, KeyboardInterrupt) as exc:
        print(f"\nRun stopped: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        # Teardown: CH1 (solar) off first, CH2 (battery) off last, then errors.
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
            samples_file.close()
        except Exception:  # noqa: BLE001
            pass

    if summary_rows:
        with summary_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\nSamples written to {samples_path}")
        print(f"Summary written to {summary_path}\n")
        print_table(summary_rows)
        print_headline(summary_rows)
        if args.plot:
            save_plots(summary_rows, base)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
