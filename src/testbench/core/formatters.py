from __future__ import annotations

import math


def format_engineering(value: float, unit: str = "", precision: int = 3) -> str:
    """Format a float value into engineering notation with appropriate SI prefixes.

    Example:
        format_engineering(0.126246, "V") -> "126.246 mV"
        format_engineering(4.88126e-05, "V") -> "48.813 uV"
        format_engineering(16.28, "V") -> "16.280 V"
        format_engineering(0.32, "A") -> "320.000 mA"
    """
    if value == 0:
        # Avoid math.log10(0) domain error
        return f"0.{'0' * precision} {unit}".strip()

    abs_val = abs(value)
    # Find the exponent in steps of 1000
    exp = int(math.floor(math.log10(abs_val) / 3.0))

    # Match exponent to standard SI prefixes
    prefixes = {
        -5: "f",  # femto
        -4: "p",  # pico
        -3: "n",  # nano
        -2: "u",  # micro
        -1: "m",  # milli
        0: "",
        1: "k",   # kilo
        2: "M",   # mega
        3: "G",   # giga
        4: "T",   # tera
    }

    if exp in prefixes:
        prefix = prefixes[exp]
        scaled = value / (10 ** (exp * 3))
    elif exp < -5:
        prefix = "f"
        scaled = value / (10 ** -15)
    else:
        prefix = "T"
        scaled = value / (10 ** 12)

    return f"{scaled:.{precision}f} {prefix}{unit}".strip()
