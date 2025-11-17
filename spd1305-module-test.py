"""
Python module usage test
"""

from siglent_SPD1305X import SPD1305X
import time


if __name__ == '__main__':

    psu = SPD1305X()

    if not psu.online:
        print("Cannot find Siglent SPD1305X")
        sys.exit(1)
    else:
        print("PSU found")


    # setpoint 1
    psu.set_voltage(1.5)
    time.sleep(0.5)
    psu.set_current(0.003)

    time.sleep(1)

    # Set display on
    psu.turn_on_display()

    time.sleep(1)

    # power on
    psu.turn_on_power()

    time.sleep(1)

    # setpoint 2
    psu.set_voltage(3.3)
    time.sleep(0.5)
    psu.set_current(0.1)

    # Wait for graph to update
    time.sleep(2)

    # setpoint 3
    psu.set_voltage(1.8)
    time.sleep(0.1)
    psu.set_current(0.01)

    time.sleep(2)

    # power off
    psu.turn_off_power()

    time.sleep(5)

    # display off
    psu.turn_off_display()
