"""Long-duration battery-charge observation: B2902B source/ammeter + 34461A voltmeter.

Physical setup
--------------
* SMU CH1 -> board solar input Vsol/GND (4-wire remote sense): rectangle
  panel emulation.  Voltage source at ``--vsol`` (default 7.2 V) with
  ``:SENS1:CURR:PROT:POS`` = ``--ch1-ilim`` (default 0.60 A) as the
  irradiance/short-circuit limit and ``:SENS1:CURR:PROT:NEG 0.01`` so the
  emulated panel can never drive current INTO the board input.
* SMU CH2 -> wired IN SERIES with the battery positive lead (HI to battery+,
  LO to the board Vbat terminal): voltage source at 0.000 V = zero-burden
  ammeter.  Fixed current measure range (smallest B2902B range >=
  ``--ch2-ilim``; DC ranges include 1 A, 1.5 A, 3 A) and symmetric
  ``:SENS2:CURR:PROT:LEV`` = ``--ch2-ilim`` (default 1.35 A).  The compliance
  doubles as a battery overcurrent guard: if it trips, CH2 inserts voltage
  and limits the loop current.  2-wire is fine here (CH2 regulates ~0 V).
* Keysight 34461A directly across the battery terminals: true V_bat via
  ``configure_voltage_dc()`` + ``read()`` each sample (high-Z input).

SIGN CONVENTION: with charge current flowing board -> (CH2 LO) -> (CH2 HI) ->
battery+, the current CH2 reports may be either sign depending on the
instrument's source convention.  The SIGNED value is logged as ``i2_raw`` and
``i_chg = --sign * i2_raw`` (``--sign`` in {+1,-1}, default +1).  The first
sample is printed prominently; if i_chg is negative while the battery is
charging, rerun with ``--sign -1``.  A persistently negative i_chg is treated
as a wiring/sign WARNING, not an abort.

Safety invariants (hard-coded):
  * Refuses ``--vbat-max`` above ``VBAT_ABS_MAX`` (3.65 V) -- this monitors a
    REAL battery, not an emulated one.
  * Refuses to start if the resting V_bat is already above --vbat-max or
    below 2.5 V (over-discharged or miswired DMM).
  * Output-off mode HIZ on both channels (turning outputs off opens the
    battery series loop -- safe).
  * Auto-output-on DISABLED on both channels (:OUTP<n>:ON:AUTO OFF): after
    *RST it is ON, so any READ?/MEAS? with the output off would silently
    auto-enable the output.  With it off, a premature READ? instead times
    out with instrument error 212 ("output not on") -- never read before
    output-on.
  * CH1 (solar) on FIRST -- this board boots from the charger and cannot
    boot from a dead battery (brownout-cycles its battery-connect FET);
    CH2 (series ammeter) on second, after --ch2-delay, with a |V2| <= 50 mV
    zero-burden readback given a settle window for boot/connect inrush.
  * Watchdog every sample: V_bat ceiling, |i_chg| ceiling, CH2 compliance
    trip, consecutive comm failures, wall-clock ceiling.

All argument validation happens before pyvisa or any instrument driver is
imported (the SMU driver constructor issues *RST on connect).
"""

from __future__ import annotations

import argparse
import csv
import math
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_RESOURCE = "USB0::10893::37377::MY60440156::0::INSTR"

VBAT_ABS_MAX = 3.65  # hard refusal ceiling for a directly-monitored real battery
VBAT_MIN_START = 2.5  # below this resting V_bat: over-discharged or miswired
READBACK_TOL_V = 0.050  # CH1 output-on setpoint readback tolerance
CH2_ZERO_TOL_V = 0.050  # CH2 output-on zero-burden readback tolerance
CH2_READBACK_WINDOW_S = 30.0  # settle window for the zero-burden check (boot inrush)
CURVE_MIN_LIMIT_A = 1e-4  # replay floor: never command a ~0 A compliance
CURVE_TRACK_TOL_A = 1e-6  # absolute change that triggers a compliance update
OVERCURRENT_PERSIST_SAMPLES = 3  # consecutive overcurrent samples before abort
MIN_INTERVAL_S = 0.5
MAX_COMM_FAILURES = 3  # consecutive failed samples before the watchdog trips
SIGN_WARN_SAMPLES = 10  # persistent-negative i_chg warning threshold
CV_EST_MIN_ELAPSED_S = 600.0  # CC->CV heuristic armed only after 10 min
CV_EST_FRACTION = 0.9  # ... i_chg below 90% of its running max
P_IN_EFF_FLOOR_W = 0.010  # eff_inst guarded below 10 mW input power

# B2902B DC current measurement ranges (A).
CURRENT_RANGES = (1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 1.5, 3.0)

DEFAULT_VSOL = 7.2
DEFAULT_CH1_ILIM = 0.60
DEFAULT_CH1_NEG_LIMIT = 0.01
DEFAULT_CH2_ILIM = 1.35
DEFAULT_INTERVAL = 2.0
DEFAULT_NPLC = 1.0
DEFAULT_DMM_NPLC = 1.0
DEFAULT_VBAT_MAX = 3.60
DEFAULT_TERM_CURRENT = 0.02
DEFAULT_TERM_MINUTES = 5.0
DEFAULT_TERM_VBAT = 3.40
DEFAULT_MAX_HOURS = 8.0
DEFAULT_PRINT_EVERY = 30
DEFAULT_TIMEOUT_MS = 30000

MEASURE_ELEMENTS = ("VOLT", "CURR", "TIME", "STAT")

CSV_COLUMNS = (
    "sample", "t_iso", "elapsed_s", "v1_set", "v1", "i1", "p_in_w", "v2", "i2_raw", "i_chg",
    "v_bat", "p_bat_w", "ah_cum", "wh_cum", "eff_inst", "flags",
)


class PanelCurve:
    """Measured panel I-V curve with a monotone I->V lookup for emulation."""

    def __init__(self, points: list, voc: float, isc: float) -> None:
        # points: (volts, amps) with amps positive = panel sourcing, V ascending.
        self.points = points
        self.voc = voc
        self.isc = isc
        vmp, imp = max(points, key=lambda t: t[0] * t[1])
        self.vmp, self.imp, self.pmp = vmp, imp, vmp * imp

    def current_at(self, volts: float) -> float:
        """Panel current at a terminal voltage: clamped linear interpolation.

        This is the replay primitive: the source is commanded to Voc and its
        compliance is set to current_at(achieved V), so the charger pulls the
        rail down and the current follows the curve like a real panel.
        """
        pts = self.points
        if volts <= pts[0][0]:
            return pts[0][1]
        if volts >= pts[-1][0]:
            return pts[-1][1]
        for (v0, i0), (v1, i1) in zip(pts, pts[1:]):
            if volts <= v1:
                if v1 == v0:
                    return i1
                return i0 + (i1 - i0) * (volts - v0) / (v1 - v0)
        return pts[-1][1]

    def v_panel(self, current: float) -> float:
        """Voltage a real panel would sit at for the measured current draw."""
        if current <= 0.0:
            return self.voc
        if current >= self.isc:
            return min(v for v, _ in self.points)
        # I decreases monotonically with V over the useful region; walk from
        # high V down and interpolate the first crossing.
        prev_v, prev_i = self.points[-1]
        for v, i in reversed(self.points[:-1]):
            if prev_i <= current <= i:
                if i == prev_i:
                    return v
                frac = (current - prev_i) / (i - prev_i)
                return prev_v + frac * (v - prev_v)
            prev_v, prev_i = v, i
        return self.voc


def load_panel_curve(path: str) -> PanelCurve:
    """Load a panel I-V capture: embedded-mcp panel-iv .json/.csv.

    JSON carries voc_v/isc_a and points[].volts/amps (amps positive =
    sourcing). CSV has volts,amps columns; sign is auto-detected.
    """
    raw: list = []
    voc = isc = None
    if path.endswith(".json"):
        import json

        data = json.load(open(path))
        voc = data.get("voc_v")
        isc = data.get("isc_a")
        for p in data["points"]:
            raw.append((float(p["volts"]), float(p["amps"])))
    else:
        import csv as _csv

        with open(path) as f:
            for r in _csv.DictReader(f):
                try:
                    raw.append((float(r["volts"]), float(r["amps"])))
                except (KeyError, ValueError):
                    continue
        currents = sorted(i for _, i in raw)
        if currents and currents[len(currents) // 2] < 0:
            raw = [(v, -i) for v, i in raw]  # log_solar_sweep sign convention
    pts = sorted(
        (v, i) for v, i in raw
        if abs(v) < 1e30 and abs(i) < 1e30 and i >= 0.0 and v >= 0.0
    )
    if len(pts) < 10:
        raise ValueError(f"panel curve {path}: only {len(pts)} usable points")
    if isc is None:
        isc = max(i for _, i in pts)
    if voc is None:
        voc = max(v for v, _ in pts)
        prev = None
        for v, i in pts:
            if prev is not None and prev[1] > 0 >= i:
                voc = prev[0] + (v - prev[0]) * prev[1] / (prev[1] - i)
                break
            prev = (v, i)
    return PanelCurve(pts, float(voc), float(isc))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Log a battery charge cycle: B2902B CH1 emulates the solar input, "
            "CH2 is a series zero-burden ammeter in the battery lead, and a "
            "Keysight 34461A reads true V_bat across the terminals."
        )
    )
    parser.add_argument(
        "--resource",
        default=DEFAULT_RESOURCE,
        help=f"VISA resource string for the SMU (default: {DEFAULT_RESOURCE})",
    )
    parser.add_argument(
        "--dmm-resource",
        default=None,
        help="VISA resource string for the 34461A (default: discovery by '34461')",
    )
    parser.add_argument(
        "--description",
        help=(
            "Free-form label that becomes part of the CSV file name "
            "(default: current date/time stamp)"
        ),
    )
    parser.add_argument(
        "--vsol",
        type=float,
        default=DEFAULT_VSOL,
        help=f"CH1 solar emulation voltage (default: {DEFAULT_VSOL} V)",
    )
    parser.add_argument(
        "--ch2-delay",
        type=float,
        default=3.0,
        help=(
            "Seconds between solar-on (CH1) and battery-loop-on (CH2), giving "
            "the board time to boot from the charger (default: 3.0)"
        ),
    )
    parser.add_argument(
        "--curve",
        default=None,
        help=(
            "Path to a measured panel I-V capture (embedded-mcp panel-iv "
            ".json/.csv). CH1 then FOLLOWS the measured curve (damped "
            "software loop) instead of the --vsol/--ch1-ilim rectangle; "
            "those two args are ignored and derived from the curve."
        ),
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.4,
        help="curve mode: setpoint relaxation factor per tick (default: 0.4)",
    )
    parser.add_argument(
        "--curve-tick",
        type=float,
        default=0.0,
        help=(
            "curve mode: extra sleep between compliance re-derivations "
            "(default: 0 = run flat out, as the working bench recipe does)"
        ),
    )
    parser.add_argument(
        "--ch1-ilim",
        type=float,
        default=DEFAULT_CH1_ILIM,
        help=f"CH1 positive current compliance / irradiance limit (default: {DEFAULT_CH1_ILIM} A)",
    )
    parser.add_argument(
        "--ch2-ilim",
        type=float,
        default=DEFAULT_CH2_ILIM,
        help=(
            "CH2 symmetric current compliance = battery overcurrent guard "
            f"(default: {DEFAULT_CH2_ILIM} A)"
        ),
    )
    parser.add_argument(
        "--ch2-range",
        type=float,
        default=None,
        help=(
            "CH2 fixed current measure range in A "
            "(default: smallest B2902B range >= --ch2-ilim, i.e. 1.5 A for the defaults)"
        ),
    )
    parser.add_argument(
        "--sign",
        type=int,
        choices=(1, -1),
        default=1,
        help="Charge-current sign: i_chg = sign * i2_raw (default: +1)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"Seconds between samples (default: {DEFAULT_INTERVAL}, minimum {MIN_INTERVAL_S})",
    )
    parser.add_argument(
        "--nplc",
        type=float,
        default=DEFAULT_NPLC,
        help=f"SMU integration time, NPLC, both channels (default: {DEFAULT_NPLC})",
    )
    parser.add_argument(
        "--dmm-nplc",
        type=float,
        default=DEFAULT_DMM_NPLC,
        help=f"34461A integration time, NPLC (default: {DEFAULT_DMM_NPLC})",
    )
    parser.add_argument(
        "--vbat-max",
        type=float,
        default=DEFAULT_VBAT_MAX,
        help=(
            f"V_bat watchdog ceiling (default: {DEFAULT_VBAT_MAX} V; "
            f"REFUSED above the hard ceiling {VBAT_ABS_MAX} V)"
        ),
    )
    parser.add_argument(
        "--i-max",
        type=float,
        default=None,
        help="|i_chg| abort threshold (default: --ch2-ilim)",
    )
    parser.add_argument(
        "--term-current",
        type=float,
        default=DEFAULT_TERM_CURRENT,
        help=f"Charge-complete taper current (default: {DEFAULT_TERM_CURRENT} A)",
    )
    parser.add_argument(
        "--term-minutes",
        type=float,
        default=DEFAULT_TERM_MINUTES,
        help=(
            "Sustained minutes below --term-current that count as charge "
            f"complete (default: {DEFAULT_TERM_MINUTES})"
        ),
    )
    parser.add_argument(
        "--term-vbat",
        type=float,
        default=DEFAULT_TERM_VBAT,
        help=(
            "Termination detection is armed only once V_bat has reached this "
            f"(default: {DEFAULT_TERM_VBAT} V)"
        ),
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=DEFAULT_MAX_HOURS,
        help=f"Wall-clock watchdog ceiling in hours (default: {DEFAULT_MAX_HOURS})",
    )
    parser.add_argument(
        "--no-dmm",
        action="store_true",
        help=(
            "Log without the 34461A: no V_bat column, DISABLES the V_bat "
            "watchdog and voltage-armed termination detection"
        ),
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=DEFAULT_PRINT_EVERY,
        help=f"Console status line every N samples (default: {DEFAULT_PRINT_EVERY})",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save PNGs next to the CSV (matplotlib savefig, no GUI)",
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

    Pure function: must be called (and must pass) BEFORE pyvisa or any
    instrument driver is imported, because constructing the SMU driver
    issues *RST to an instrument that may be wired to a live battery.
    """
    errors: list[str] = []

    if args.vbat_max > VBAT_ABS_MAX:
        errors.append(
            f"REFUSED: --vbat-max={args.vbat_max} exceeds VBAT_ABS_MAX={VBAT_ABS_MAX} V "
            "(hard safety ceiling for a directly-monitored real battery; will not clamp)"
        )
    if args.vbat_max <= 0:
        errors.append(f"--vbat-max must be positive (got {args.vbat_max})")
    if args.interval < MIN_INTERVAL_S:
        errors.append(
            f"--interval must be >= {MIN_INTERVAL_S} s (got {args.interval}); "
            "faster polling starves the watchdog checks"
        )

    for name, value in (
        ("--vsol", args.vsol),
        ("--ch1-ilim", args.ch1_ilim),
        ("--ch2-ilim", args.ch2_ilim),
        ("--nplc", args.nplc),
        ("--dmm-nplc", args.dmm_nplc),
        ("--term-current", args.term_current),
        ("--term-minutes", args.term_minutes),
        ("--term-vbat", args.term_vbat),
        ("--max-hours", args.max_hours),
    ):
        if value <= 0:
            errors.append(f"{name} must be positive (got {value})")

    if args.ch1_ilim > 3.0:
        errors.append(f"--ch1-ilim must be <= 3.0 A DC (got {args.ch1_ilim})")
    if args.ch2_ilim > 3.0:
        errors.append(f"--ch2-ilim must be <= 3.0 A DC (got {args.ch2_ilim})")
    if args.ch2_range is not None and args.ch2_range < args.ch2_ilim:
        errors.append(
            f"--ch2-range={args.ch2_range} is below --ch2-ilim={args.ch2_ilim} "
            "(the fixed measure range must cover the compliance level)"
        )
    if args.i_max is not None and args.i_max <= 0:
        errors.append(f"--i-max must be positive (got {args.i_max})")
    if args.term_vbat > args.vbat_max:
        errors.append(
            f"--term-vbat={args.term_vbat} is above --vbat-max={args.vbat_max}; "
            "termination detection would never arm before the watchdog trips"
        )
    if args.print_every < 1:
        errors.append("--print-every must be >= 1")
    if args.timeout_ms <= 0:
        errors.append("--timeout-ms must be positive")
    if args.curve is not None:
        import os

        if not os.path.isfile(args.curve):
            errors.append(f"--curve file not found: {args.curve}")
        if not (0.0 < args.alpha <= 1.0):
            errors.append(f"--alpha must be in (0, 1] (got {args.alpha})")
        if args.curve_tick < 0.0:
            errors.append(f"--curve-tick must be >= 0 (got {args.curve_tick})")
        if args.curve_tick > args.interval:
            errors.append(
                f"--curve-tick={args.curve_tick} must not exceed --interval={args.interval}"
            )
    return errors


def pick_fixed_current_range(min_amps: float) -> float:
    """Smallest B2902B DC current range >= min_amps (caps at the largest)."""
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


class ChargeAccumulator:
    """Trapezoidal Ah/Wh integration over an irregular sample stream (pure)."""

    def __init__(self) -> None:
        self.ah = 0.0  # charge into the battery
        self.wh = 0.0  # energy into the battery (needs v_bat)
        self.wh_in = 0.0  # energy from the solar side
        self._prev: tuple[float, float, float | None, float] | None = None

    def add(self, t_s: float, i_chg: float, p_bat_w: float | None, p_in_w: float) -> None:
        if self._prev is not None:
            prev_t, prev_i, prev_p_bat, prev_p_in = self._prev
            dt = t_s - prev_t
            if dt > 0:
                self.ah += 0.5 * (i_chg + prev_i) * dt / 3600.0
                self.wh_in += 0.5 * (p_in_w + prev_p_in) * dt / 3600.0
                if p_bat_w is not None and prev_p_bat is not None:
                    self.wh += 0.5 * (p_bat_w + prev_p_bat) * dt / 3600.0
        self._prev = (t_s, i_chg, p_bat_w, p_in_w)


class TerminationDetector:
    """Charge-complete detection: sustained taper current, voltage-armed (pure).

    Arms once v_bat >= term_vbat (latched); after that, a continuous window of
    i_chg < term_current lasting term_seconds means CHARGE_COMPLETE.  With the
    DMM disabled (v_bat always None) it never arms.
    """

    def __init__(self, term_vbat: float, term_current: float, term_seconds: float) -> None:
        self.term_vbat = term_vbat
        self.term_current = term_current
        self.term_seconds = term_seconds
        self.armed = False
        self.below_since: float | None = None

    def update(self, elapsed_s: float, v_bat: float | None, i_chg: float) -> bool:
        if not self.armed:
            if v_bat is not None and v_bat >= self.term_vbat:
                self.armed = True
            else:
                return False
        if i_chg < self.term_current:
            if self.below_since is None:
                self.below_since = elapsed_s
            return elapsed_s - self.below_since >= self.term_seconds
        self.below_since = None
        return False


class CvTransitionEstimator:
    """Heuristic CC->CV transition detector (pure; clearly an ESTIMATE).

    Reports the first time i_chg drops below CV_EST_FRACTION of its running
    maximum after CV_EST_MIN_ELAPSED_S, plus the maximum v_bat observed.
    """

    def __init__(self) -> None:
        self.i_chg_max = 0.0
        self.v_bat_max: float | None = None
        self.cv_elapsed_s: float | None = None
        self.cv_v_bat: float | None = None

    def update(self, elapsed_s: float, v_bat: float | None, i_chg: float) -> bool:
        """Returns True on the sample where the CV estimate first latches."""
        if v_bat is not None and (self.v_bat_max is None or v_bat > self.v_bat_max):
            self.v_bat_max = v_bat
        latched = False
        if (
            self.cv_elapsed_s is None
            and elapsed_s >= CV_EST_MIN_ELAPSED_S
            and self.i_chg_max > 0
            and i_chg < CV_EST_FRACTION * self.i_chg_max
        ):
            self.cv_elapsed_s = elapsed_s
            self.cv_v_bat = v_bat
            latched = True
        self.i_chg_max = max(self.i_chg_max, i_chg)
        return latched


def watchdog_reason(
    v_bat: float | None,
    i_chg: float,
    ch2_tripped: bool,
    consecutive_comm_failures: int,
    elapsed_s: float,
    vbat_max: float,
    i_max: float,
    max_hours: float,
) -> str | None:
    """First tripped watchdog, or None (pure).

    i_chg/compliance conditions are PERSISTENT counts (consecutive samples),
    not instantaneous: a boot-looping board legitimately rails the current
    guard in transients while the charger brings the rail up.  The compliance
    limit itself protects the battery during those transients; the watchdog
    aborts only if the overcurrent condition is sustained.
    """
    if v_bat is not None and v_bat > vbat_max:
        return f"VBAT_MAX: V_bat {v_bat:.4f} V > --vbat-max {vbat_max} V"
    if abs(i_chg) > i_max:
        return f"I_MAX: |i_chg| {abs(i_chg):.4f} A > --i-max {i_max} A"
    if ch2_tripped:
        return "CH2_COMPLIANCE: series ammeter compliance tripped (battery overcurrent guard)"
    if consecutive_comm_failures > MAX_COMM_FAILURES:
        return f"COMM: {consecutive_comm_failures} consecutive communication failures"
    if elapsed_s > max_hours * 3600.0:
        return f"MAX_HOURS: elapsed {elapsed_s / 3600.0:.2f} h > --max-hours {max_hours}"
    return None


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Everything below touches instruments (imports deferred into the connectors).
# ---------------------------------------------------------------------------

def connect_dmm(args: argparse.Namespace):
    """Connect + configure the 34461A; returns (dmm, resting v_bat)."""
    from testbench.core.scpi import SCPISettings
    from testbench.dmm.keysight_34461a import Keysight34461A

    settings = SCPISettings(timeout_ms=args.timeout_ms)
    dmm = Keysight34461A(settings=settings, resource_name=args.dmm_resource)
    if not dmm.online():
        raise RuntimeError(
            "34461A not found (discovery by '34461'); pass --dmm-resource or --no-dmm"
        )
    print(f"DMM: {dmm.identify().strip()}")
    dmm.configure_voltage_dc(range_v=10.0, nplc=args.dmm_nplc, high_impedance=True)
    v_bat = dmm.read()
    codes = dmm.error_codes()
    if codes:
        raise RuntimeError(f"ABORT: 34461A error queue not clean after configure: {codes}")
    return dmm, v_bat


class StopRequest:
    """Deferred Ctrl-C / SIGTERM / SIGHUP.

    An asynchronous KeyboardInterrupt lands inside a USBTMC transfer (the
    replay loop is in one almost continuously), leaves the instrument with a
    half-sent message or unread response, and every teardown command after
    that either does nothing or waits out the full VISA timeout -- the
    outputs stay on while the process looks hung. So the first signal only
    raises a flag that the sample loop checks between transactions; a second
    Ctrl-C forces the old behaviour.
    """

    def __init__(self) -> None:
        self.requested = False
        self.count = 0

    def __call__(self, signum, frame) -> None:  # noqa: ANN001
        self.count += 1
        if self.count == 1:
            self.requested = True
            safe_print(
                "\nstop requested: finishing the current sample, then outputs off "
                "(press Ctrl-C again to force)",
                err=True,
            )
        else:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt


def safe_print(msg: str, err: bool = False) -> None:
    """print() that survives a dead pipe (tee killed by the same Ctrl-C)."""
    try:
        print(msg, file=sys.stderr if err else sys.stdout, flush=True)
    except (BrokenPipeError, OSError):
        pass


def connect_smu(args: argparse.Namespace):
    from testbench.core.scpi import SCPISettings
    from testbench.sourcemeter.keysight_b2902b import KeysightB2902B

    settings = SCPISettings(timeout_ms=args.timeout_ms)
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        raise RuntimeError("B2902B not found")
    return smu


def check_error_queue(smu, context: str) -> None:
    codes = [code for code in smu.system.read_error_codes() if code != 0]
    if codes:
        raise RuntimeError(f"ABORT: instrument error queue not clean {context}: {codes}")


def configure_channels(smu, args: argparse.Namespace, ch2_range: float,
                       vsol_init: float, ch1_ilim: float) -> None:
    """Program both channels while outputs are off (constructor already *RST)."""
    driver = smu.driver
    driver.write("*CLS")
    smu.system.clear_error_queue()

    # Output-off mode HIZ on both channels before any output-on.  For CH2
    # (series ammeter) HIZ-off simply opens the battery loop -- safe.
    smu.output.set_output_off_mode("HIZ", channel=1)
    smu.output.set_output_off_mode("HIZ", channel=2)

    # After *RST, :OUTP<n>:ON:AUTO is ON: a READ?/MEAS? with the output off
    # would silently AUTO-ENABLE the output.  Disable it before anything else;
    # a premature READ? then fails with instrument error 212 ("output not on")
    # instead of energizing the channel.
    smu.output.set_auto_output_on_enabled(False, channel=1)
    smu.output.set_auto_output_on_enabled(False, channel=2)

    # :OUTP[c]:LOW resets to GROund -- the LOW terminal is tied to chassis.
    # With CH1 LO on board GND and CH2 LO in the battery lead, that shorts the
    # battery node to ground THROUGH THE INSTRUMENT. Float both (outputs off).
    driver.write(":OUTP1:LOW FLO")
    driver.write(":OUTP2:LOW FLO")

    # CH2 = series zero-burden ammeter (configured first; it turns on first).
    driver.write(":SOUR2:FUNC:MODE VOLT")
    driver.write(":SOUR2:VOLT:MODE FIX")
    driver.write(":SOUR2:VOLT:RANG 20")  # readback must never overrange in CC
    driver.write(":SOUR2:VOLT:LEV 0.0")
    # READ? = :INIT + :FETC?, and :INIT fires the transient trigger, which
    # jumps the source to its TRIGGERED level (default 0 V). Pin it equal to
    # the immediate level so a measurement never moves the source.
    driver.write(":SOUR2:VOLT:TRIG 0.0")
    # One combined write: a second :FUNC replaces (not adds to) the list.
    driver.write(':SENS2:FUNC "VOLT","CURR"')
    smu.sense.enable_remote_sense(False, channel=2)  # 2-wire: it regulates ~0 V
    driver.write(f":SENS2:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS2:CURR:NPLC {args.nplc}")
    driver.write(":SENS2:CURR:RANG:AUTO OFF")
    driver.write(f":SENS2:CURR:RANG {ch2_range}")
    # Symmetric compliance = battery overcurrent guard in either direction.
    driver.write(f":SENS2:CURR:PROT:LEV {args.ch2_ilim}")

    # CH1 = solar rectangle emulation (4-wire).
    driver.write(":SOUR1:FUNC:MODE VOLT")
    driver.write(":SOUR1:VOLT:MODE FIX")
    driver.write(":SOUR1:VOLT:RANG 20")
    driver.write(f":SOUR1:VOLT:LEV {vsol_init}")
    driver.write(f":SOUR1:VOLT:TRIG {vsol_init}")  # see CH2 note: reads must not move the source
    # One combined write: a second :FUNC replaces (not adds to) the list.
    driver.write(':SENS1:FUNC "VOLT","CURR"')
    smu.sense.enable_remote_sense(True, channel=1)
    driver.write(f":SENS1:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS1:CURR:NPLC {args.nplc}")
    driver.write(":SENS1:CURR:RANG:AUTO OFF")
    driver.write(f":SENS1:CURR:RANG {pick_fixed_current_range(1.2 * ch1_ilim)}")
    # Asymmetric: sources up to the emulation limit, never INTO the board input.
    driver.write(f":SENS1:CURR:PROT:POS {ch1_ilim}")
    driver.write(f":SENS1:CURR:PROT:NEG {DEFAULT_CH1_NEG_LIMIT}")

    driver.write(":TRIG1:ACQ:COUN 1")
    driver.write(":TRIG2:ACQ:COUN 1")

    check_error_queue(smu, "after channel configuration")


def outputs_on(smu, args: argparse.Namespace, vsol_init: float) -> None:
    """CH1 (solar) FIRST so the board boots from the charger, then CH2.

    This board cannot boot from a dead battery: connecting the battery loop
    with no solar present brownout-cycles the battery-connect FET at the
    CH2 compliance limit.  Sun first, battery second.
    """
    smu.driver.write(":OUTP1 ON")
    time.sleep(0.2)
    record = smu.read_measurements([1], MEASURE_ELEMENTS)[0]
    v1 = record.get("VOLT")
    # CH1 is a panel emulator: sag below the setpoint under load is normal.
    # Informational only -- hardware compliance is the protection.
    print(f"CH1 on: {v1} V (set {vsol_init:.4f} V; sag under load is panel behavior)")

    if args.ch2_delay > 0:
        print(f"waiting {args.ch2_delay:.1f} s for the board to boot from solar...")
        time.sleep(args.ch2_delay)

    smu.driver.write(":OUTP2 ON")
    time.sleep(0.3)
    record = smu.read_measurements([2], MEASURE_ELEMENTS)[0]
    v2 = record.get("VOLT")
    print(f"CH2 on: {v2} V across the ammeter (0 near-zero = healthy loop; "
          "boot-loop transients are expected and bounded by compliance)")


def acquire_sample(smu, dmm) -> tuple[dict, dict, float | None, bool]:
    """One synchronized SMU read + DMM read + CH2 trip check."""
    records = smu.read_measurements([1, 2], MEASURE_ELEMENTS)
    ch1 = {"volt": records[0].get("VOLT"), "curr": records[0].get("CURR")}
    ch2 = {"volt": records[1].get("VOLT"), "curr": records[1].get("CURR")}
    v_bat = dmm.read() if dmm is not None else None
    ch2_tripped = smu.sense.compliance_tripped("CURR", channel=2)
    return ch1, ch2, v_bat, ch2_tripped


def save_plots(history: list[dict], base: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not history:
        print("No samples to plot.")
        return
    hours = [row["elapsed_s"] / 3600.0 for row in history]

    # V_bat + i_chg vs elapsed hours (twin axes).
    fig, ax1 = plt.subplots(figsize=(8, 5))
    vbat_pts = [(h, r["v_bat"]) for h, r in zip(hours, history) if r["v_bat"] is not None]
    if vbat_pts:
        ax1.plot([p[0] for p in vbat_pts], [p[1] for p in vbat_pts],
                 color="tab:blue", label="V_bat")
    ax1.set_xlabel("Elapsed (h)")
    ax1.set_ylabel("V_bat (V)", color="tab:blue")
    ax1.grid(True, alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(hours, [r["i_chg"] for r in history], color="tab:red", label="i_chg")
    ax2.set_ylabel("i_chg (A)", color="tab:red")
    ax1.set_title("Battery charge: V_bat and i_chg")
    path1 = base.with_name(base.name + "_vbat_ichg.png")
    fig.savefig(path1, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # eff_inst + p_in vs time (twin axes).
    fig, ax1 = plt.subplots(figsize=(8, 5))
    eff_pts = [
        (h, r["eff_inst"] * 100.0)
        for h, r in zip(hours, history)
        if r["eff_inst"] is not None and not math.isnan(r["eff_inst"])
    ]
    if eff_pts:
        ax1.plot([p[0] for p in eff_pts], [p[1] for p in eff_pts],
                 color="tab:green", label="eff_inst")
    ax1.set_xlabel("Elapsed (h)")
    ax1.set_ylabel("Instantaneous efficiency (%)", color="tab:green")
    ax1.grid(True, alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(hours, [r["p_in_w"] for r in history], color="tab:orange", label="p_in")
    ax2.set_ylabel("P_in (W)", color="tab:orange")
    ax1.set_title("Battery charge: efficiency and input power")
    path2 = base.with_name(base.name + "_eff_pin.png")
    fig.savefig(path2, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plots written to {path1} and {path2}")


def main() -> int:
    args = parse_args()

    # --- validation: MUST precede any pyvisa import / instrument connection ---
    errors = validate_args(args)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    i_max = args.i_max if args.i_max is not None else args.ch2_ilim
    ch2_range = (
        args.ch2_range if args.ch2_range is not None
        else pick_fixed_current_range(args.ch2_ilim)
    )

    curve = None
    if args.curve is not None:
        try:
            curve = load_panel_curve(args.curve)
        except Exception as exc:  # noqa: BLE001
            print(f"error: --curve load failed: {exc}", file=sys.stderr)
            return 2
        vsol_init = curve.voc
        ch1_ilim = 1.1 * curve.isc
        print(
            f"Panel curve {args.curve}: Voc {curve.voc:.3f} V, Isc {curve.isc:.4f} A, "
            f"MPP {curve.pmp:.4f} W @ {curve.vmp:.3f} V / {curve.imp:.4f} A "
            f"({len(curve.points)} points) -- CH1 follows this curve "
            f"(alpha {args.alpha}, tick {args.curve_tick} s)"
        )
    else:
        vsol_init = args.vsol
        ch1_ilim = args.ch1_ilim
    description = sanitize_description(args.description)
    base = Path("data") / f"charge_log_{description}"
    base.parent.mkdir(parents=True, exist_ok=True)
    csv_path = base.with_name(base.name + ".csv")
    summary_path = base.with_name(base.name + "_summary.csv")

    if args.no_dmm:
        print(
            "WARNING: --no-dmm -- no true V_bat measurement.  The V_bat "
            "watchdog is DISABLED and termination detection will never arm "
            "(it is voltage-armed).  The run only stops on i_chg/compliance/"
            "comm watchdogs, --max-hours, or Ctrl-C."
        )

    # --- DMM first: sanity-check the resting battery voltage ---
    dmm = None
    if not args.no_dmm:
        try:
            dmm, v_rest = connect_dmm(args)
        except Exception as exc:  # noqa: BLE001
            print(f"error: DMM connection failed: {exc}", file=sys.stderr)
            return 1
        print(f"Resting V_bat: {v_rest:.4f} V")
        if v_rest > args.vbat_max:
            print(
                f"NOTE: resting V_bat {v_rest:.4f} V is above --vbat-max "
                f"{args.vbat_max} V -- proceeding (charger decides)."
            )
        if v_rest < VBAT_MIN_START:
            # Not a refusal: a protected pack with its FET latched open reads
            # ~0 V, and waking it is the charger's job (part of the test).
            print(
                f"NOTE: resting V_bat {v_rest:.4f} V is below {VBAT_MIN_START} V "
                "(protection latched / deeply discharged) -- proceeding; the "
                "charger is expected to recover it."
            )

    # --- SMU (constructor issues *RST) ---
    try:
        smu = connect_smu(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: SMU connection failed: {exc}", file=sys.stderr)
        return 1

    accumulator = ChargeAccumulator()
    terminator = TerminationDetector(
        args.term_vbat, args.term_current, args.term_minutes * 60.0
    )
    cv_estimator = CvTransitionEstimator()
    history: list[dict] = []
    stop_reason = "UNKNOWN"
    exit_code = 0
    final_v_bat: float | None = None
    negative_streak = 0
    sign_warned = False
    curve_loops = 0
    curve_updates = 0

    csv_file = csv_path.open("w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow(CSV_COLUMNS)
    csv_file.flush()

    stop = StopRequest()
    forced_stop = False
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    try:
        configure_channels(smu, args, ch2_range, vsol_init, ch1_ilim)
        ch1_desc = (
            f"curve follower from Voc {vsol_init:.3f} V" if curve is not None
            else f"{vsol_init} V rectangle"
        )
        print(
            f"CH2 series ammeter: 0.000 V source, range {ch2_range} A, "
            f"compliance +/-{args.ch2_ilim} A | CH1 solar: {ch1_desc}, "
            f"+{ch1_ilim:.4f}/-{DEFAULT_CH1_NEG_LIMIT} A"
        )
        outputs_on(smu, args, vsol_init)
        # DMM display left alone so it shows live V_bat at the bench.

        start = time.time()
        sample = 0
        consecutive_comm_failures = 0
        overcurrent_streak = 0
        v1_set = vsol_init  # replay mode: the level never moves from Voc
        ch1_limit = ch1_ilim  # replay mode: re-derived from achieved V each loop
        curve_loops = 0
        curve_updates = 0
        while True:
            if stop.requested:
                stop_reason = "USER_STOP: Ctrl-C (clean stop at sample boundary)"
                print(stop_reason)
                break
            sample += 1
            target = start + (sample - 1) * args.interval
            if curve is None:
                delay = target - time.time()
                if delay > 0:
                    time.sleep(delay)
            else:
                # Panel replay (the recipe that works against this board,
                # from embedded-mcp hold_at_panel): the source LEVEL stays at
                # Voc and never moves; the current COMPLIANCE is re-derived from
                # the achieved voltage on every loop, so when the charger pulls
                # the rail down the available current follows the measured
                # curve exactly as a real panel's would. Runs flat out: an SMU
                # in compliance is a perfect current source (dI/dV = 0), and
                # dI/dV is what the MPPT perturbs to see, so slow updates make
                # the tracker hunt. Absolute 1 uA tolerance -- a 50 mV MPPT
                # step only moves this panel's current by tens of uA.
                while True:
                    remaining = target - time.time()
                    if remaining <= 0 or stop.requested:
                        break
                    if args.curve_tick > 0:
                        time.sleep(min(args.curve_tick, remaining))
                    try:
                        rec = smu.read_measurements([1], ["VOLT", "CURR"])[0]
                        v_achieved = rec.get("VOLT")
                        if v_achieved is None or abs(v_achieved) > 1e30:
                            continue
                        want = max(CURVE_MIN_LIMIT_A, curve.current_at(v_achieved))
                        if abs(want - ch1_limit) > CURVE_TRACK_TOL_A:
                            ch1_limit = want
                            smu.driver.write(f":SENS1:CURR:PROT:POS {ch1_limit:.6f}")
                            curve_updates += 1
                        curve_loops += 1
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:  # noqa: BLE001 - sample read handles persistent failures
                        print(f"curve loop skipped: {exc}", file=sys.stderr)
                        break

            flags: list[str] = []
            try:
                ch1, ch2, v_bat, ch2_tripped = acquire_sample(smu, dmm)
                consecutive_comm_failures = 0
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - VISA errors vary by backend
                flags.append("COMM_RETRY")
                print(f"comm failure (retrying once): {exc}", file=sys.stderr)
                try:
                    ch1, ch2, v_bat, ch2_tripped = acquire_sample(smu, dmm)
                    consecutive_comm_failures = 0
                except KeyboardInterrupt:
                    raise
                except Exception as exc2:  # noqa: BLE001
                    consecutive_comm_failures += 1
                    print(
                        f"comm failure #{consecutive_comm_failures} (sample skipped): {exc2}",
                        file=sys.stderr,
                    )
                    if consecutive_comm_failures > MAX_COMM_FAILURES:
                        stop_reason = watchdog_reason(
                            None, 0.0, False, consecutive_comm_failures,
                            0.0, args.vbat_max, i_max, args.max_hours,
                        )
                        exit_code = 1
                        break
                    continue

            now = time.time()
            elapsed = now - start
            v1 = ch1["volt"] or 0.0
            i1 = ch1["curr"] or 0.0
            i2_raw = ch2["curr"] or 0.0
            i_chg = args.sign * i2_raw
            p_in = v1 * i1
            p_bat = v_bat * i_chg if v_bat is not None else None
            eff_inst = (
                p_bat / p_in
                if p_bat is not None and p_in > P_IN_EFF_FLOOR_W
                else float("nan")
            )
            if v_bat is not None:
                final_v_bat = v_bat

            accumulator.add(elapsed, i_chg, p_bat, p_in)

            if sample == 1:
                print("=" * 72)
                print(
                    f"FIRST SAMPLE: i_chg = {i_chg:+.4f} A (i2_raw {i2_raw:+.4f} A, "
                    f"--sign {args.sign:+d}) -- if this is negative while the "
                    "battery is charging, rerun with --sign -1"
                )
                print("=" * 72)
            if i_chg < 0:
                negative_streak += 1
                if negative_streak >= SIGN_WARN_SAMPLES and not sign_warned:
                    sign_warned = True
                    print(
                        f"WARNING: i_chg has been negative for {negative_streak} "
                        "consecutive samples -- wiring/sign problem? (Expected "
                        "positive while charging; rerun with --sign -1 if so. "
                        "Continuing -- discharge is a valid observation.)"
                    )
            else:
                negative_streak = 0
            if negative_streak >= SIGN_WARN_SAMPLES:
                flags.append("SIGN_WARN")

            if cv_estimator.update(elapsed, v_bat, i_chg):
                flags.append("CC_TO_CV_EST")
                print(
                    f"[{format_elapsed(elapsed)}] CC->CV transition ESTIMATE "
                    f"(heuristic: i_chg below {CV_EST_FRACTION:.0%} of its max "
                    f"after {CV_EST_MIN_ELAPSED_S / 60:.0f} min): i_chg {i_chg:.4f} A, "
                    f"V_bat {v_bat if v_bat is not None else float('nan'):.4f} V"
                )

            was_armed = terminator.armed
            complete = terminator.update(elapsed, v_bat, i_chg)
            if terminator.armed and not was_armed:
                flags.append("TERM_ARMED")
                print(
                    f"[{format_elapsed(elapsed)}] termination detection armed "
                    f"(V_bat {v_bat:.4f} V >= --term-vbat {args.term_vbat} V)"
                )

            row = {
                "sample": sample,
                "t_iso": datetime.now().isoformat(timespec="milliseconds"),
                "elapsed_s": round(elapsed, 3),
                "v1_set": round(v1_set, 4),
                "v1": v1,
                "i1": i1,
                "p_in_w": p_in,
                "v2": ch2["volt"],
                "i2_raw": i2_raw,
                "i_chg": i_chg,
                "v_bat": v_bat,
                "p_bat_w": p_bat,
                "ah_cum": accumulator.ah,
                "wh_cum": accumulator.wh,
                "eff_inst": eff_inst,
                "flags": "|".join(flags),
            }
            writer.writerow([row[col] for col in CSV_COLUMNS])
            csv_file.flush()  # hours-long run must survive interruption
            history.append(row)

            if sample == 1 or sample % args.print_every == 0 or flags:
                vbat_text = f"{v_bat:.4f} V" if v_bat is not None else "n/a"
                eff_text = f"{eff_inst * 100:.1f}%" if not math.isnan(eff_inst) else "n/a"
                print(
                    f"[{format_elapsed(elapsed)}] #{sample} V_bat={vbat_text} "
                    f"i_chg={i_chg:+.4f} A P_in={p_in:.3f} W "
                    f"Ah={accumulator.ah:.4f} Wh={accumulator.wh:.4f} "
                    f"eff={eff_text} flags={'|'.join(flags) or '-'}"
                )

            reason = watchdog_reason(
                v_bat, i_chg, ch2_tripped, consecutive_comm_failures,
                elapsed, args.vbat_max, i_max, args.max_hours,
            )
            if reason and (reason.startswith("I_MAX") or reason.startswith("CH2_COMPLIANCE")):
                # Non-fatal: the charger decides the current; log it only.
                flags.append("OVERCURRENT")
                reason = None
            if reason and reason.startswith("VBAT_MAX"):
                # Non-fatal: the charger decides the voltage; log it only.
                flags.append("VBAT_HIGH")
                reason = None
            if reason:
                stop_reason = reason
                exit_code = 1
                print(f"WATCHDOG TRIP: {reason}", file=sys.stderr)
                break
            if complete:
                stop_reason = (
                    f"CHARGE_COMPLETE: i_chg < {args.term_current} A sustained for "
                    f"{args.term_minutes} min (armed at V_bat >= {args.term_vbat} V)"
                )
                print(stop_reason)
                break
    except KeyboardInterrupt:
        forced_stop = True
        stop_reason = "USER_STOP: forced KeyboardInterrupt (mid-transaction)"
        safe_print(f"\n{stop_reason}")
    except Exception as exc:  # noqa: BLE001 - abort with teardown
        stop_reason = f"ERROR: {exc}"
        safe_print(f"\nRun stopped: {exc}", err=True)
        exit_code = 1
    finally:
        # Teardown: CH1 (solar) off first, then CH2 (HIZ opens the battery
        # series loop -- safe), then diagnostics; each step in its own try.
        # A wedged USBTMC session must not turn this into 30 s per step:
        # short timeout, and after a forced stop a device clear to resync.
        resource = getattr(smu.driver, "resource", None)
        try:
            if resource is not None:
                resource.timeout = 3000
                if forced_stop:
                    resource.clear()
        except Exception as exc:  # noqa: BLE001
            safe_print(f"teardown: device clear failed: {exc}", err=True)
        try:
            smu.driver.write(":OUTP1 OFF")
        except Exception as exc:  # noqa: BLE001 - best-effort teardown
            safe_print(f"teardown: OUTP1 OFF failed: {exc}", err=True)
        try:
            smu.driver.write(":OUTP2 OFF")
        except Exception as exc:  # noqa: BLE001
            safe_print(f"teardown: OUTP2 OFF failed: {exc}", err=True)
        try:
            st1 = smu.driver.query(":OUTP1?")
            st2 = smu.driver.query(":OUTP2?")
            safe_print(f"outputs at exit: CH1={st1} CH2={st2} (0 = off)")
        except Exception as exc:  # noqa: BLE001
            safe_print(
                f"teardown: output readback failed: {exc} -- CHECK THE FRONT PANEL",
                err=True,
            )
        try:
            codes = [code for code in smu.system.read_error_codes() if code != 0]
            safe_print(f"SMU error queue at exit: {codes if codes else 'clean'}")
        except Exception as exc:  # noqa: BLE001
            safe_print(f"teardown: SMU error-queue read failed: {exc}", err=True)
        if dmm is not None:
            try:
                dmm.clear_display()
            except Exception:  # noqa: BLE001
                pass
            try:
                codes = dmm.error_codes()
                print(f"DMM error queue at exit: {codes if codes else 'clean'}")
            except Exception as exc:  # noqa: BLE001
                print(f"teardown: DMM error-queue read failed: {exc}", file=sys.stderr)
        try:
            csv_file.close()
        except Exception:  # noqa: BLE001
            pass

    # --- summary ---
    duration_s = history[-1]["elapsed_s"] if history else 0.0
    eff_overall = (
        accumulator.wh / accumulator.wh_in if accumulator.wh_in > 0 else float("nan")
    )
    summary = {
        "duration_s": round(duration_s, 1),
        "samples": len(history),
        "final_v_bat": final_v_bat,
        "max_v_bat": cv_estimator.v_bat_max,
        "ah_in": accumulator.ah,
        "wh_bat": accumulator.wh,
        "wh_solar": accumulator.wh_in,
        "eff_overall": eff_overall,
        "cv_transition_est_s": cv_estimator.cv_elapsed_s,
        "stop_reason": stop_reason,
    }
    print("\n--- summary ---")
    print(f"duration:        {format_elapsed(duration_s)} ({len(history)} samples)")
    print(f"final V_bat:     {final_v_bat if final_v_bat is not None else 'n/a'}")
    print(f"max V_bat:       {cv_estimator.v_bat_max if cv_estimator.v_bat_max is not None else 'n/a'}")
    print(f"charge in:       {accumulator.ah:.4f} Ah")
    print(f"energy to batt:  {accumulator.wh:.4f} Wh")
    print(f"energy from sol: {accumulator.wh_in:.4f} Wh")
    print(f"overall eff:     {eff_overall * 100:.1f}%" if not math.isnan(eff_overall)
          else "overall eff:     n/a")
    if cv_estimator.cv_elapsed_s is not None:
        print(f"CC->CV ESTIMATE: t={format_elapsed(cv_estimator.cv_elapsed_s)} (heuristic)")
    print(f"stop reason:     {stop_reason}")
    if curve is not None and duration_s > 0:
        print(
            f"panel replay:    {curve_loops} loops, {curve_updates} compliance updates, "
            f"{curve_loops / duration_s:.1f} Hz (if the charger perturbs faster than "
            "this, the emulation lags and the numbers say less than they look)"
        )

    if history:
        with summary_path.open("w", newline="") as handle:
            summary_writer = csv.DictWriter(handle, fieldnames=list(summary))
            summary_writer.writeheader()
            summary_writer.writerow(summary)
        print(f"\nSamples written to {csv_path}")
        print(f"Summary written to {summary_path}")
        if args.plot:
            save_plots(history, base)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
