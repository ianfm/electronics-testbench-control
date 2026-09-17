"""Bounded spot-sampler for an in-progress charge held by the SMU.

Use when a long-lived logger cannot be kept alive: the B2902B keeps its last
configuration and output state on its own, so this opens a session, samples
CH1/CH2 (READ? (@1,2)) and the 34461A for --seconds, appends rows in the
b2900_charge_log.py CSV layout, and closes. It NEVER changes source settings.

It reports whether the end-of-charge rule is met over the window (i_chg below
--term-current for the whole window with V_bat >= --term-vbat). With
--off-when-done it turns both outputs off in that case.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

SMU = "USB0::10893::37377::MY60440156::0::INSTR"
DMM = "USB0::10893::4865::MY53222530::0::INSTR"
COLUMNS = ("sample", "t_iso", "elapsed_s", "v1_set", "v1", "i1", "p_in_w", "v2", "i2_raw", "i_chg",
           "v_bat", "p_bat_w", "ah_cum", "wh_cum", "eff_inst", "flags")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--t0-iso", default=None, help="run start (ISO) for elapsed_s; default: first row of --csv or now")
    ap.add_argument("--term-current", type=float, default=0.02)
    ap.add_argument("--term-vbat", type=float, default=3.40)
    ap.add_argument("--off-when-done", action="store_true")
    ap.add_argument("--force-off", action="store_true", help="turn both outputs off after sampling regardless")
    args = ap.parse_args()

    import pyvisa
    rm = pyvisa.ResourceManager()
    smu = rm.open_resource(SMU); smu.timeout = 10000
    dmm = rm.open_resource(DMM); dmm.timeout = 10000
    smu.write(":FORM:DATA ASC"); smu.write(":FORM:ELEM:SENS VOLT,CURR")
    dmm.write("CONF:VOLT:DC 10"); dmm.write("VOLT:DC:NPLC 1"); dmm.write("VOLT:DC:IMP:AUTO ON")
    dmm.write("TRIG:SOUR IMM"); dmm.write("SAMP:COUN 1")

    path = Path(args.csv)
    rows_prev = []
    if path.exists():
        rows_prev = list(csv.DictReader(open(path)))
    t0 = datetime.fromisoformat(args.t0_iso) if args.t0_iso else (
        datetime.fromisoformat(rows_prev[0]["t_iso"]) if rows_prev else datetime.now())
    ah = float(rows_prev[-1]["ah_cum"]) if rows_prev else 0.0
    wh = float(rows_prev[-1]["wh_cum"]) if rows_prev else 0.0
    sample = int(rows_prev[-1]["sample"]) if rows_prev else 0
    v1_set = float(smu.query(":SOUR1:VOLT?"))
    on1, on2 = smu.query(":OUTP1?").strip(), smu.query(":OUTP2?").strip()
    print(f"outputs CH1={on1} CH2={on2}; CH1 level {v1_set:.3f} V; appending to {path.name} from sample {sample}")

    new_file = not path.exists()
    f = open(path, "a", newline=""); w = csv.writer(f)
    if new_file:
        w.writerow(COLUMNS)
    window = []
    t_start = time.time(); last_t = None
    try:
        while time.time() - t_start < args.seconds:
            r = smu.query("READ? (@1,2)").strip().split(",")
            v1, i1, v2, i2 = (float(x) for x in r[:4])
            v_bat = float(dmm.query("READ?"))
            now = datetime.now(); elapsed = (now - t0).total_seconds()
            i_chg = i2; p_in = v1 * i1; p_bat = v_bat * i_chg
            if last_t is not None:
                dt = (elapsed - last_t) / 3600.0
                ah += i_chg * dt; wh += p_bat * dt
            last_t = elapsed; sample += 1
            eff = p_bat / p_in if p_in > 0.01 else float("nan")
            w.writerow([sample, now.isoformat(timespec="milliseconds"), round(elapsed, 3), round(v1_set, 4), v1, i1, p_in,
                        v2, i2, i_chg, v_bat, p_bat, ah, wh, eff, "SPOT"])
            f.flush()
            window.append((v_bat, i_chg, p_in))
            time.sleep(max(0.0, args.interval - 0.3))
    finally:
        f.close()

    vb = [x[0] for x in window]; ic = [x[1] for x in window]; pi = [x[2] for x in window]
    print(f"{len(window)} samples over {args.seconds:.0f} s: V_bat {vb[0]:.4f}->{vb[-1]:.4f} V (max {max(vb):.4f}), "
          f"i_chg mean {statistics.mean(ic)*1e3:.1f} mA (max {max(ic)*1e3:.1f}), P_in mean {statistics.mean(pi)*1e3:.0f} mW, "
          f"Ah {ah:.4f}, Wh {wh:.3f}")
    done = (max(ic) < args.term_current) and (min(vb) >= args.term_vbat)
    print(f"end-of-charge rule (i_chg < {args.term_current*1e3:.0f} mA for whole window, V_bat >= {args.term_vbat}): "
          f"{'MET' if done else 'not met'}")
    if args.force_off or (done and args.off_when_done):
        smu.write(":OUTP1 OFF"); smu.write(":OUTP2 OFF")
        print("outputs:", smu.query(":OUTP1?").strip(), smu.query(":OUTP2?").strip())
    smu.close(); dmm.close()
    return 0 if not done else 3


if __name__ == "__main__":
    sys.exit(main())
