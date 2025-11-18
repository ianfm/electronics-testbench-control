"""
Python module usage test
"""

from testbench.psu.siglent_spd1305x import SiglentSPD1305X as SPD1305X
import sys
import time


if __name__ == '__main__':

    psu = SPD1305X()

    if not psu.online():
        print("Cannot find Siglent SPD1305X")
        sys.exit(1)
    else:
        print("PSU found")


    # setpoint 1
    channel = 1
    psu.set_voltage(channel, 1.5)
    time.sleep(0.5)
    psu.set_current(channel, 0.003)

    time.sleep(1)

    # Set display on
    psu.turn_on_display()

    time.sleep(1)

    # power on
    psu.output_on(channel)

    time.sleep(1)

    # setpoint 2
    psu.set_voltage(channel, 3.3)
    time.sleep(0.5)
    psu.set_current(channel, 0.1)

    # Wait for graph to update
    time.sleep(2)

    # setpoint 3
    psu.set_voltage(channel, 1.8)
    time.sleep(0.1)
    psu.set_current(channel, 0.01)

    time.sleep(2)

    # power off
    psu.output_off(channel)

    time.sleep(5)

    # display off
    psu.turn_off_display()
