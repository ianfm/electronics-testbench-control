#!/usr/bin/env python3
"""Render charge-cycle analysis JSON (from analyze_charge_cycle.py) as Markdown.

Markdown renders on GitHub and can be linked from an issue tracker, unlike
the HTML reports, which need a browser opened on the file.

  build_charge_report_md.py RUN.json OUT.md [--title T] [--board B]
      one run: summary table, run notes, links to the logger's PNGs

  build_charge_report_md.py --compare A.json B.json OUT.md --names "A name" "B name"
      two runs side by side, plus a five-panel comparison figure written
      next to OUT.md (same stem, .png)
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def hms(s: float) -> str:
    h = int(s // 3600)
    m = int(s % 3600 // 60)
    return f"{h} h {m:02d} min"


def pct(x) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.1f} %"


def num(x, d=2, unit="") -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{d}f}{unit}"


def value_at(series: list[dict], key: str, t: float):
    """Series value at elapsed time t (first sample at or after t), or None."""
    for p in series:
        if p["t"] >= t:
            return p.get(key)
    return None


def run_stem(d: dict) -> str:
    """charge_log_<description>, whether the analysis ran on the raw or the _flagged CSV."""
    return Path(d["source"]).stem.removesuffix("_flagged")


def report_name(d: dict) -> str:
    return "charge_report_" + run_stem(d).removeprefix("charge_log_") + ".md"


def rows_for(summary: dict, series: list[dict]) -> list[tuple[str, str]]:
    s = summary
    mah_10h = value_at(series, "ah", 36000)
    return [
        ("Start → end", f"{s['start_iso'][:16].replace('T', ' ')} → {s['end_iso'][:16].replace('T', ' ')}"),
        ("Cycle time to termination", hms(s["duration_s"])),
        ("Terminated by", f"taper: i_chg < {s['term_floor_a'] * 1e3:.0f} mA for 5 min" if s.get("terminated_by_taper") else "wall clock / other"),
        ("Samples", f"{s['samples']:,} (2 s)"),
        ("Pack voltage at loop close → peak → final", f"{num(s['v_bat_start'], 3)} → {num(s['v_bat_max'], 3)} → {num(s['v_bat_final'], 3)} V"),
        ("Constant-current plateau", f"{s['i_plateau_a'] * 1e3:.0f} mA for {hms(s['cc_end_s'])} ({s['cc_fraction'] * 100:.0f} % of cycle)"),
        ("Input operating point (CC)", f"{num(s['v1_cc'], 2)} V / {s['i1_cc'] * 1e3:.0f} mA / {s['p_in_cc_w'] * 1e3:.0f} mW"),
        ("Harvest of panel Pmax (CC)", pct(s["harvest_fraction"])),
        ("Charge delivered by 10 h", f"{mah_10h * 1e3:.0f} mAh" if mah_10h is not None else "—"),
        ("Charge delivered at termination", f"{s['ah_in'] * 1e3:.0f} mAh"),
        ("Energy into pack", num(s["wh_bat"], 2, " Wh")),
        ("Energy from panel", num(s["wh_in"], 2, " Wh")),
        ("Efficiency, overall", pct(s["eff_overall"])),
        ("Efficiency, constant-current phase", pct(s["eff_cc"])),
        ("Efficiency, excl. replay-hunt rows", pct(s.get("eff_excluding_hunt"))),
        ("Replay-hunt time (flagged)", f"{s['hunt_hours']:.1f} h" if s.get("hunt_hours") is not None else "—"),
        ("Final current at termination", f"{s['i_final_a'] * 1e3:.1f} mA"),
    ]


FIXTURE = (
    "Keysight B2902B CH1 replays the measured panel I-V curve "
    "`panel-iv-2026-08-24_145423` (Voc 7.71 V, Isc 0.194 A, Pmax 1.10 W at 6.25 V): "
    "source level parked at Voc, current compliance re-derived from the achieved voltage "
    "on every loop, so the charger sets the operating point. B2902B CH2 is a zero-burden "
    "series ammeter in the battery+ lead (0 V source, 3 A range). A Keysight 34461A reads "
    "the pack voltage directly at its terminals. Solar on first, battery loop second. "
    "Energy integrals are trapezoidal over consecutive 2 s samples (gaps > 10 s skipped); "
    "efficiency is Wh into the pack ÷ Wh from the panel over the same intervals."
)


def single(run: Path, out: Path, title: str, board: str) -> None:
    d = json.loads(run.read_text())
    s, series = d["summary"], d["series"]
    stem = run_stem(d)
    lines = [f"# {title}", "", f"**Board:** {board}  ", f"**Raw log:** [`{stem}.csv`]({stem}.csv) · hunt-flagged copy [`{stem}_flagged.csv`]({stem}_flagged.csv) · analysis [`{run.name}`]({run.name})", ""]
    lines += ["## Summary", "", "| | |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows_for(s, series)]
    lines += ["", "## Plots", "", f"![Pack voltage and charge current]({stem}_vbat_ichg.png)", "", f"![Efficiency and input power]({stem}_eff_pin.png)", ""]
    if s.get("notes"):
        lines += ["## Notes", ""] + [f"- {n}" for n in s["notes"]] + [""]
    lines += ["## Fixture", "", FIXTURE, ""]
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


def compare(a: Path, b: Path, out: Path, names: tuple[str, str], title: str) -> None:
    A, B = json.loads(a.read_text()), json.loads(b.read_text())
    fig = out.with_suffix(".png")
    draw_compare(A, B, names, fig)
    ra, rb = rows_for(A["summary"], A["series"]), rows_for(B["summary"], B["series"])
    lines = [f"# {title}", "",
             "Two ON1006 boards, identical fixture and cell: the LTC3130 revision and the original "
             "LT3652 revision, each charging the same protection-latched LiFePO4 pack from 0 V "
             "terminals to termination against the same measured panel curve. Both runs are single "
             "uninterrupted logs, run back to back on the same cell with the pack discharged to its "
             "protection latch before each.", "",
             f"![Comparison]({fig.name})", "",
             "## Summary", "", f"| | {names[0]} | {names[1]} |", "|---|---|---|"]
    lines += [f"| {ka} | {va} | {vb} |" for (ka, va), (_, vb) in zip(ra, rb)]
    lines += ["", "## What is the same", "",
              "- Same pack (protected LiFePO4), same latched 0 V start, same emulated panel curve and fixture, same 20 mA / 5 min end-of-charge rule.",
              "- Both chargers unlatched the pack on the first sample and drew essentially the panel's full available power throughout the constant-current phase (≈1.1 W at ≈6.1 V), so charge current was panel-limited on both boards.",
              "", "## What differs", "",
              "- Neither run exercises a precharge region: the LTC3130 has none, and the LT3652's 300 mA precharge current (15 % of its 2 A programmed maximum) exceeds what this panel can deliver, so its current was panel-limited from the start. Demonstrating the LT3652 precharge step needs a source able to supply more than 300 mA.",
              "- The two boards' float voltages differ (≈3.48 V vs ≈3.59 V), so termination lands at different pack voltages.",
              "- Efficiency: the LTC3130 (synchronous buck-boost) runs ~86 % panel-to-pack; the LT3652 (non-synchronous buck with a Schottky rectifier at a 3.3 V output) runs ~73 %.",
              "- The emulated panel is a software compliance replay. It is faithful while a charger holds a steady operating point and hunts once the charger's draw falls in CV; hunt rows are flagged `REPLAY_HUNT` in the `_flagged.csv` files and excluded from the \"excl. hunt\" efficiency. The CC phase of both runs is clean.",
              "", "## Per-run reports", "",
              f"- {names[0]}: [{report_name(A)}]({report_name(A)}) · raw log [`{run_stem(A)}.csv`]({run_stem(A)}.csv)",
              f"- {names[1]}: [{report_name(B)}]({report_name(B)}) · raw log [`{run_stem(B)}.csv`]({run_stem(B)}.csv)",
              "", "## Fixture", "", FIXTURE, ""]
    out.write_text("\n".join(lines))
    print(f"wrote {out} and {fig}")


def draw_compare(A: dict, B: dict, names: tuple[str, str], fig_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = ("#2a78d6", "#eb6834")
    panels = [
        ("v_bat", "Pack voltage (V)", 1, None),
        ("i_chg", "Charge current (mA)", 1e3, 0),
        ("p_in", "Power from panel (mW)", 1e3, 0),
        ("ah", "Cumulative charge (mAh)", 1e3, 0),
        ("eff", "Efficiency (%)", 1, 0),
    ]
    fig, axes = plt.subplots(len(panels), 1, figsize=(10, 13), sharex=True)
    for d in (A, B):
        for p in d["series"]:
            p["eff"] = (p["v_bat"] * p["i_chg"]) / p["p_in"] * 100 if p["p_in"] > 0.01 and p["v_bat"] > 0 else None
    for ax, (key, label, scale, lo) in zip(axes, panels):
        for d, name, c in zip((A, B), names, colors):
            pts = [(p["t"] / 3600, p[key] * scale) for p in d["series"] if p.get(key) is not None]
            ax.plot([x for x, _ in pts], [y for _, y in pts], color=c, lw=1.2, label=name)
        ax.set_ylabel(label)
        ax.grid(True, alpha=0.3)
        if lo is not None:
            ax.set_ylim(bottom=lo)
        if key == "eff":
            ax.set_ylim(0, 100)
    axes[0].legend(loc="lower right")
    axes[-1].set_xlabel("Time since battery loop closed (h)")
    fig.suptitle("ON1006 charger comparison — same cell, same emulated panel, same latched start", y=0.995)
    fig.tight_layout()
    fig.savefig(fig_path, dpi=130)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="RUN.json OUT.md, or (with --compare) A.json B.json OUT.md")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--names", nargs=2, default=("A", "B"))
    ap.add_argument("--title", default=None)
    ap.add_argument("--board", default="")
    args = ap.parse_args()
    if args.compare:
        a, b, out = (Path(x) for x in args.inputs)
        compare(a, b, out, tuple(args.names), args.title or "Charger comparison")
    else:
        run, out = (Path(x) for x in args.inputs)
        single(run, out, args.title or run.stem, args.board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
