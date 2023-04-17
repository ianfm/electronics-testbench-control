"""
test-equipment-control\siglent_SPD1305X.py
Single-channel PSU
Implement basic PSU functionality


TODO: set channel to 1 always
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
            if "SPD13" in device:
                # print("[PSU] PSU found - " + str(device))
                self.psu = rm.open_resource(device)
                self.psu.write_termination = "\n"
                time.sleep(0.05)
                self.psu.write("*RST")
                time.sleep(0.05)
                # print("[PSU] PSU reset - " + str(device))
        if self.psu is None:
            print("[PSU] ERROR - No PSU found")

    def online(self) -> bool:
        return self.psu is not None

    def set_voltage(self, channel_number: int, volts: float) -> bool:
        """Sets voltage of specified channel

        Args:
            channel_number (int): channel number of psu
            volts (float): volts to set

        Returns:
            bool: if command is successful
        """
        if self.psu is not None:
            try:
                self.psu.write("CH" + str(channel_number) + ":VOLT " + str(volts))
                time.sleep(0.05)
                self.psu.write("CH" + str(channel_number) + ":VOLT " + str(volts))
                time.sleep(0.05)
                self.psu.write("CH" + str(channel_number) + ":VOLT?")
                time.sleep(0.05)
                reported_voltage = self.psu.read_raw(1024).decode("utf-8")
                if float(volts) == float(reported_voltage):
                    return True
                else:
                    print("[PSU] Power Supply voltage not getting set")
                    return False
            except pyvisa.errors.VisaIOError:
                print("Something bad happened - voltage")
                return False
        else:
            print("[PSU] No Device Connected")
            return False

    def set_current(self, channel_number: int, current: float) -> bool:
        """Sets current of specified channel

        Args:
            channel_number (int): channel number of psu
            amps (float): amps to set

        Returns:
            bool: if command is successful
        """
        if self.psu is not None:
            self.psu.write("CH" + str(channel_number) + ":CURR " + str(current))
            time.sleep(0.05)
            self.psu.write("CH" + str(channel_number) + ":CURR " + str(current))
            time.sleep(0.05)
            self.psu.write("CH" + str(channel_number) + ":CURR?")
            time.sleep(0.05)
            reported_current = self.psu.read_raw(1024).decode("utf-8")
            if float(current) == float(reported_current):
                return True
            else:
                print("[PSU] Power Supply current not getting set")
                return False
        else:
            print("[PSU] No Device Connected")
            return False

    def turn_on_power(self, channel_number: int) -> bool:
        """Turns on power to the specified channel

        Args:
            channel_number (int): channel number of psu

        Returns:
            bool: if command is successful
        """
        if self.psu is not None:
            self.psu.write("OUTP CH" + str(channel_number) + ",ON")
            time.sleep(0.05)
            self.psu.write("OUTP CH" + str(channel_number) + ",ON")
            time.sleep(0.05)
            return True
        else:
            print("[PSU] No Device Connected")
            return False

    def turn_off_power(self, channel_number: int) -> bool:
        """Turns off power to the specified channel

        Args:
            channel_number (int): channel number of psu

        Returns:
            bool: if command is successful
        """
        if self.psu is not None:
            self.psu.write("OUTP CH" + str(channel_number) + ",OFF")
            time.sleep(0.05)
            self.psu.write("OUTP CH" + str(channel_number) + ",OFF")
            time.sleep(0.05)
            return True
        else:
            print("[PSU] No Device Connected")
            return False

    def turn_on_display(self, channel_number: int = 1) -> bool:
        """Turns on display

        Args:
            channel_number (int, optional): channel number of psu.
                                            Defaults to 1

        Returns:
            bool: if command is successful
        """
        if self.psu is not None:
            self.psu.write("OUTP:WAVE CH" + str(channel_number) + ",ON")
            time.sleep(0.05)
            self.psu.write("OUTP:WAVE CH" + str(channel_number) + ",ON")
            time.sleep(0.05)
            return True
        else:
            print("[PSU] No Device Connected")
            return False

    def turn_off_display(self, channel_number: int = 1) -> bool:
        """Turns off display

        Args:
            channel_number (int, optional): channel number of psu.
                                            Defaults to 1

        Returns:
            bool: if command is successful
        """
        if self.psu is not None:
            self.psu.write("OUTP:WAVE CH" + str(channel_number) + ",OFF")
            time.sleep(0.05)
            self.psu.write("OUTP:WAVE CH" + str(channel_number) + ",OFF")
            time.sleep(0.05)
            return True
        else:
            print("[PSU] No Device Connected")
            return False



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
        print("Cannot find Siglent SPD1305X")
        sys.exit(1)
    else:
        print("PSU found")


    channel = 1
    if args.set_channel is not None:
        if (args.set_channel == 1):
            channel = args.set_channel
            print("Channel set to " + str(channel))
        else:
            print("Invalid channel. Channel may optionally be specified as 1, but other values are invalid")
    
    if not channel:
        print("No channel")
        sys.exit(0)
    
    if args.display_on:
        print("Turning on display")
        psu.turn_on_display(channel)
    elif args.display_off:
        print("Turning off display")
        psu.turn_off_display(channel)

    voltage = None
    if args.set_voltage is not None:
        voltage = args.set_voltage
        print("Channel " + str(channel) + " voltage set to " + str(voltage) + "V")
        psu.set_voltage(channel, voltage)
    
    current = None
    if args.set_current is not None:
        current = args.set_current
        print("Channel " + str(channel) + " current set to " + str(current) + "A")
        psu.set_current(channel, current)

    if args.power_on:
        print("Turning on channel " + str(channel))
        psu.turn_on_power(channel)
    elif args.power_off:
        print("Turning off channel " + str(channel))
        psu.turn_off_power(channel)

    sys.exit(0)
