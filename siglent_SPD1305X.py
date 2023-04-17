"""
test-equipment-control\siglent_SPD1305X.py
Single-channel PSU
Implement basic PSU functionality
"""

import pyvisa
import time
import sys
import argparse



class SPD1305X:
    def __init__(self):
        pyvisa.query_delay = 0.05
        self.psu = None
        rm = pyvisa.ResourceManager()
        for device in rm.list_resources():
            if "SPD1X" in device:
                # print("[PSU] PSU found - " + str(device))
                self.psu = rm.open_resource(device)
                self.psu.write_termination = "\n"
                time.sleep(0.05)
                self.psu.write("*RST")
                time.sleep(0.05)
        if self.psu is None:
            print("[PSU] ERROR - No PSU found")

    def online(self) -> bool:
        return self.psu is not None





if __name__ == '__main__':
    parser = argparse.ArgumentParser(
    description="Controls a Siglent SPD3303X programmable DC power supply"
    )
    parser.add_argument("--set-channel", type=int, action="store", required=False, help="Channel to modify")

    parser.add_argument("--set-voltage", type=float, action="store", required=False, help="Voltage to set the specified channel")

    parser.add_argument("--set-current", type=float, action="store", required=False, help="Current to set the specified channel")

    power_set_group = parser.add_mutually_exclusive_group()

    power_set_group.add_argument("--power-on", action="store_true", required=False, help="Turn on the specified channel")

    power_set_group.add_argument("--power-off", action="store_true", required=False, help="Turn off the specified channel")

    display_set_group = parser.add_mutually_exclusive_group()

    display_set_group.add_argument("--display-on", action="store_true", required=False, help="Turn on display on the specified channel")

    display_set_group.add_argument("--display-off", action="store_true", required=False, help="Turn off display on the specified channel")

    args = parser.parse_args()
    psu = SPD1305X()

    if not psu.online:
        print("Cannot find Siglent SPD3303X")
        sys.exit(1)

    print("PSU found")

    # channel = None
    # if args.set_channel is not None:
    #     if (args.set_channel == 1 or args.set_channel == 2):
    #         channel = args.set_channel
    #         print("Channel set to " + str(channel))
    #     else:
    #         print("Invalid channel. Must be 1 or 2")
    
    # if not channel:
    #     print("No channel")
    #     sys.exit(0)
    
    # if args.display_on:
    #     print("Turning on display")
    #     psu.turn_on_display(channel)
    # elif args.display_off:
    #     print("Turning off display")
    #     psu.turn_off_display(channel)

    # voltage = None
    # if args.set_voltage is not None:
    #     voltage = args.set_voltage
    #     print("Channel " + str(channel) + " voltage set to " + str(voltage) + "V")
    #     psu.set_voltage(channel, voltage)
    
    # current = None
    # if args.set_current is not None:
    #     current = args.set_current
    #     print("Channel " + str(channel) + " current set to " + str(current) + "A")
    #     psu.set_current(channel, current)

    # if args.power_on:
    #     print("Turning on channel " + str(channel))
    #     psu.turn_on_power(channel)
    # elif args.power_off:
    #     print("Turning off channel " + str(channel))
    #     psu.turn_off_power(channel)

    sys.exit(0)
