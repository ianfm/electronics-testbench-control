"""Quick connectivity check for a Keysight B2902B over USB."""

import pyvisa


RESOURCE = "USB0::10893::45313::MY60440156::0::INSTR"


def main() -> None:
    rm = pyvisa.ResourceManager()
    instrument = rm.open_resource(RESOURCE, timeout=5000)
    instrument.write_termination = "\n"
    instrument.read_termination = "\n"

    try:
        idn = instrument.query("*IDN?")
        print(f"*IDN?: {idn}")

        instrument.write("SYST:REM")
        instrument.write("*RST")
        instrument.write("SOUR:FUNC VOLT")
        instrument.write("SOUR:VOLT 0")
        measurement = instrument.query("MEAS:CURR?")
        print(f"MEAS:CURR?: {measurement}")
    finally:
        instrument.close()
        rm.close()


if __name__ == "__main__":
    main()
