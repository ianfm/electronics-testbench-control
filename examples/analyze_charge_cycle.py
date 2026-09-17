"""Summarise a b2900_charge_log.py run: phases, totals, efficiency, downsampled series.

Reads the per-sample CSV, emits a JSON document for plotting/reporting:

  python examples/analyze_charge_cycle.py data/charge_log_<desc>.csv [--out x.json]
        [--points 600] [--pmax-w 1.098]

Phases are inferred from the data, not the log's heuristics:
  * CC   -- charge current within 10 % of its plateau (median of the first
            third of the run), battery voltage rising.
  * CV   -- battery voltage flat near its maximum while current tapers.
  * done -- current below the termination floor (default 20 mA) at the end.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path


GAP_S = 10.0  # consecutive samples further apart than this are a logging gap


def load(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "t": float(r["elapsed_s"]),
                    "iso": r["t_iso"],
                    "v1": float(r["v1"]),
                    "i1": float(r["i1"]),
                    "p_in": float(r["p_in_w"]),
                    "i_chg": float(r["i_chg"]),
                    "v_bat": float(r["v_bat"]) if r["v_bat"] not in ("", "None") else math.nan,
                    "ah": float(r["ah_cum"]),
                    "wh": float(r["wh_cum"]),
                    "flags": r.get("flags", ""),
                })
            except (KeyError, ValueError):
                continue
    return rows


def downsample(rows: list[dict], n: int) -> list[dict]:
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    out = []
    for k in range(n):
        chunk = rows[int(k * step): int((k + 1) * step)] or [rows[min(int(k * step), len(rows) - 1)]]
        agg = {"t": chunk[-1]["t"], "iso": chunk[-1]["iso"], "ah": chunk[-1]["ah"], "wh": chunk[-1]["wh"]}
        for key in ("v1", "i1", "p_in", "i_chg", "v_bat"):
            vals = [c[key] for c in chunk if not math.isnan(c[key])]
            agg[key] = statistics.mean(vals) if vals else math.nan
        out.append(agg)
    return out


def analyse(rows: list[dict], pmax_w: float | None, term_a: float) -> dict:
    t_end = rows[-1]["t"]
    i = [r["i_chg"] for r in rows]
    vb = [r["v_bat"] for r in rows if not math.isnan(r["v_bat"])]
    p_in = [r["p_in"] for r in rows]

    first_third = i[: max(1, len(i) // 3)]
    plateau = statistics.median(first_third)
    # CC phase: from start until current falls below 90 % of the plateau and
    # stays there (first index after which no sample re-exceeds the threshold).
    thresh = 0.9 * plateau
    cc_end_idx = len(rows) - 1
    for idx in range(len(rows)):
        if i[idx] < thresh and all(x < thresh for x in i[idx: idx + 30]):
            cc_end_idx = idx
            break
    cc_end_t = rows[cc_end_idx]["t"]

    v_bat_max = max(vb) if vb else math.nan
    v_bat_final = vb[-1] if vb else math.nan
    v_bat_start = vb[0] if vb else math.nan
    # First sample at which the pack is above 0.5 V = protection unlatched.
    wake_t = next((r["t"] for r in rows if not math.isnan(r["v_bat"]) and r["v_bat"] > 0.5), None)

    tail = rows[-150:]  # last ~5 min at 2 s
    i_tail = statistics.mean(r["i_chg"] for r in tail)
    terminated = i_tail < term_a

    # Energy / efficiency
    wh_in = 0.0
    for a, b in zip(rows, rows[1:]):
        if b["t"] - a["t"] > GAP_S:
            continue  # logging gap: no data, do not invent energy across it
        wh_in += 0.5 * (a["p_in"] + b["p_in"]) * (b["t"] - a["t"]) / 3600.0
    wh_bat = rows[-1]["wh"]
    ah_bat = rows[-1]["ah"]
    eff_overall = wh_bat / wh_in if wh_in > 0 else math.nan

    # Efficiency by phase (ratio of energies, not mean of ratios)
    def phase_eff(sel):
        w_in = w_out = 0.0
        for a, b in zip(sel, sel[1:]):
            if b["t"] - a["t"] > GAP_S:
                continue
            dt = (b["t"] - a["t"]) / 3600.0
            w_in += 0.5 * (a["p_in"] + b["p_in"]) * dt
            w_out += 0.5 * (a["v_bat"] * a["i_chg"] + b["v_bat"] * b["i_chg"]) * dt
        return (w_out / w_in) if w_in > 0 else math.nan

    cc_rows = rows[: cc_end_idx + 1]
    cv_rows = rows[cc_end_idx:]
    p_in_cc = statistics.mean(r["p_in"] for r in cc_rows)
    harvest = (p_in_cc / pmax_w) if pmax_w else None

    return {
        "duration_s": t_end,
        "samples": len(rows),
        "start_iso": rows[0]["iso"],
        "end_iso": rows[-1]["iso"],
        "v_bat_start": v_bat_start,
        "wake_t_s": wake_t,
        "v_bat_max": v_bat_max,
        "v_bat_final": v_bat_final,
        "i_plateau_a": plateau,
        "cc_end_s": cc_end_t,
        "cc_fraction": cc_end_t / t_end if t_end else math.nan,
        "i_final_a": i_tail,
        "terminated_by_taper": terminated,
        "term_floor_a": term_a,
        "ah_in": ah_bat,
        "wh_bat": wh_bat,
        "wh_in": wh_in,
        "eff_overall": eff_overall,
        "eff_cc": phase_eff(cc_rows) if len(cc_rows) > 2 else math.nan,
        "eff_cv": phase_eff(cv_rows) if len(cv_rows) > 2 else math.nan,
        "p_in_cc_w": p_in_cc,
        "v1_cc": statistics.mean(r["v1"] for r in cc_rows),
        "i1_cc": statistics.mean(r["i1"] for r in cc_rows),
        "pmax_w": pmax_w,
        "harvest_fraction": harvest,
        "flags": sorted({f for r in rows for f in r["flags"].split("|") if f}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default=None)
    ap.add_argument("--points", type=int, default=600)
    ap.add_argument("--pmax-w", type=float, default=1.0980707523,
                    help="panel Pmax from the replayed curve (default: panel-iv-2026-08-24_145423)")
    ap.add_argument("--term-current", type=float, default=0.02)
    args = ap.parse_args()

    rows = load(args.csv)
    if len(rows) < 10:
        print("not enough samples", file=sys.stderr)
        return 1
    summary = analyse(rows, args.pmax_w, args.term_current)
    series = downsample(rows, args.points)
    doc = {"summary": summary, "series": series, "source": str(Path(args.csv).name)}

    def clean(obj):
        # json.dump emits bare NaN (invalid JSON); map it to null instead.
        if isinstance(obj, float):
            return None if math.isnan(obj) else obj
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj

    out = args.out or str(Path(args.csv).with_suffix(".analysis.json"))
    with open(out, "w") as f:
        json.dump(clean(doc), f, indent=1, allow_nan=False)

    s = summary
    print(f"{s['samples']} samples, {s['duration_s']/3600:.2f} h  ({s['start_iso'][:19]} -> {s['end_iso'][:19]})")
    print(f"pack: {s['v_bat_start']:.4f} V -> max {s['v_bat_max']:.4f} V, final {s['v_bat_final']:.4f} V; "
          f"woke at t={s['wake_t_s']} s")
    print(f"CC plateau {s['i_plateau_a']*1e3:.0f} mA for {s['cc_end_s']/3600:.2f} h ({s['cc_fraction']*100:.0f} %); "
          f"final current {s['i_final_a']*1e3:.1f} mA, taper-terminated: {s['terminated_by_taper']}")
    print(f"in: {s['wh_in']:.3f} Wh from panel (CC mean {s['p_in_cc_w']*1e3:.0f} mW at "
          f"{s['v1_cc']:.2f} V / {s['i1_cc']*1e3:.0f} mA"
          + (f", {s['harvest_fraction']*100:.1f} % of Pmax" if s['harvest_fraction'] else "") + ")")
    print(f"out: {s['ah_in']:.4f} Ah, {s['wh_bat']:.3f} Wh into battery; efficiency overall "
          f"{s['eff_overall']*100:.1f} %  (CC {s['eff_cc']*100:.1f} %, CV {s['eff_cv']*100:.1f} %)")
    print(f"flags: {s['flags'] or 'none'};  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
