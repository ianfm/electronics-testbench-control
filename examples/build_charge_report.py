"""Inject a charge-cycle analysis JSON into the report template -> standalone HTML.

  python examples/build_charge_report.py data/charge_log_<desc>.analysis.json out.html
"""
from __future__ import annotations

import sys
from pathlib import Path

TEMPLATE = Path(__file__).with_name("charge_report_template.html")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    data = Path(sys.argv[1]).read_text()
    # A JSON payload inside <script type=application/json> must not contain "</script".
    data = data.replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("__DATA__", data)
    Path(sys.argv[2]).write_text(html)
    print(f"wrote {sys.argv[2]} ({len(html)//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
