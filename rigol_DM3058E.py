"""
test-equipment-control\rigol_DM3058E.py
Implement basic DMM functionality
"""

import pyvisa
import time
import sys
import argparse
from typing import Tuple


class DM3058E:
    def __init__(self):
        pyvisa.query_delay = 0.05
        self.dmm = None
        rm = pyvisa.ResourceManager()
        for device in rm.list_resources():
            # print("[DMM] DMM found - " + str(device))
            if "DM3" in device:
                print("[DMM] DMM found - " + str(device))
                self.dmm = rm.open_resource(device)
                self.dmm.write_termination = "\n"
                time.sleep(pyvisa.query_delay)
        if self.dmm is None:
            print("[DMM] ERROR - No DMM found")
    
    def online(self) -> bool:
        """Checks if dmm is connected

        Returns:
            bool: [description]
        """
        return self.dmm is not None

    def reset(self) -> bool:
        """Resets device to factory state

        Returns:
            bool: [description]
        """
        if self.dmm is not None:
            self.dmm.write("*RST")
            time.sleep(pyvisa.query_delay)
            return True
        else:
            print("[DMM] No Device Connected")
            return False

    # def get_configuration(self) -> Tuple[str, float, float]:
    #     """Retreieves configuration of mode, range, and resolution

    #     Returns:
    #         tuple: mode, range, resolution
    #     """
    #     if self.dmm is not None:
    #         self.dmm.write("CONF?")
    #         time.sleep(pyvisa.query_delay)
    #         reported_configuration = self.dmm.read_raw(1024).decode("utf-8")
    #         reported_configuration = reported_configuration[
    #             1 : len(reported_configuration) - 2
    #         ]
    #         reported_mode = reported_configuration[:reported_configuration.index(" ")]
    #         reported_range = float(reported_configuration[reported_configuration.index(" ") + 2:reported_configuration.index(",")])
    #         reported_resolution = float(reported_configuration[reported_configuration.index(",") + 1:])
    #         return reported_mode, reported_resolution, reported_range
    #     else:
    #         print("[DMM] No Device Connected")
    #         return "", -1, -1

    # def set_resolution(self, resolution) -> bool:
    #     """Sets resolution of measurement

    #     Returns:
    #         bool: success
    #     """
    #     if self.dmm is not None:
    #         _, _, range = self.get_configuration()
    #         for mode in ["CONF:VOLT:DC ", "CONF:CURR:DC ", "CONF:RES "]:
    #             self.dmm.write(mode + str(range) + ", " + str(resolution))
    #             time.sleep(pyvisa.query_delay)
    #             self.dmm.write(mode + str(range) + ", " + str(resolution))
    #             time.sleep(pyvisa.query_delay)
    #             self.dmm.write("CONF?")
    #             time.sleep(pyvisa.query_delay)
    #             reported_configuration = self.dmm.read_raw(1024).decode("utf-8")
    #             print(reported_configuration)
    #             reported_configuration = reported_configuration[
    #                 1 : len(reported_configuration) - 2
    #             ]
    #             reported_range = float(reported_configuration[reported_configuration.index(" ") + 1:reported_configuration.index(",")])
    #             reported_resolution = float(reported_configuration[reported_configuration.index(",") + 1:])
    #             if resolution != reported_resolution and range != reported_range:
    #                 return False
    #         return True
    #     else:
    #         print("[DMM] No Device Connected")
    #         return False
    
    # def set_range_volts(self, range) -> bool:
    #     """Sets voltage range of measurment

    #     Returns:
    #         bool: success
    #     """
    #     if self.dmm is not None:
    #         _, resolution, _ = self.get_configuration()
    #         self.dmm.write("CONF:VOLT:DC " + str(range) + ", " + str(resolution))
    #         time.sleep(pyvisa.query_delay)
    #         self.dmm.write("CONF:VOLT:DC " + str(range) + ", " + str(resolution))
    #         time.sleep(pyvisa.query_delay)
    #         self.dmm.write("CONF?")
    #         time.sleep(pyvisa.query_delay)
    #         reported_configuration = self.dmm.read_raw(1024).decode("utf-8")
    #         print(reported_configuration)
    #         reported_configuration = reported_configuration[
    #             1 : len(reported_configuration) - 2
    #         ]
    #         reported_range = float(reported_configuration[reported_configuration.index(" ") + 1:reported_configuration.index(",")])
    #         reported_resolution = float(reported_configuration[reported_configuration.index(",") + 1:])
    #         if resolution == reported_resolution and range == reported_range:
    #             return True
    #         else:
    #             return False
    #     else:
    #         print("[DMM] No Device Connected")
    #         return False

    # def set_num_samples(self, num_samples) -> bool:
    #     """Sets number of samples to take

    #     Returns:
    #         bool: [description]
    #     """
    #     if self.dmm is not None:
    #         self.dmm.write("SAMP:COUNT " + str(num_samples))
    #         time.sleep(pyvisa.query_delay)
    #         self.dmm.write("SAMP:COUNT " + str(num_samples))
    #         time.sleep(pyvisa.query_delay)
    #         self.dmm.write("SAMP:COUN?")
    #         time.sleep(pyvisa.query_delay)
    #         reported_num_samples = self.dmm.read_raw(1024).decode("utf-8")
    #         if float(reported_num_samples) == float(num_samples):
    #             return True
    #         else:
    #             return False
            
    #     else:
    #         print("[DMM] No Device Connected")
    #         return False
    
    # def get_num_samples(self) -> float:
    #     """Receives number of samples from DMM

    #     Returns:
    #         bool: [description]
    #     """
    #     if self.dmm is not None:
    #         self.dmm.write("SAMP:COUN?")
    #         time.sleep(pyvisa.query_delay)
    #         reported_num_samples = float(self.dmm.read_raw(1024).decode("utf-8"))
    #         return reported_num_samples
            
    #     else:
    #         print("[DMM] No Device Connected")
    #         return False
    
    def get_voltage(self):
        """returns measured voltage in volts

        Returns:
            bool: [description]
        """
        if self.dmm is not None:
            self.dmm.write("MEAS:VOLT:DC?")
            time.sleep(pyvisa.query_delay)
            voltage = float(self.dmm.read_raw(1024).decode("utf-8"))
            return voltage
            
        else:
            print("[DMM] No Device Connected")
            return False

    def get_current(self):
        """returns measured current in amps

        Returns:
            bool: [description]
        """
        if self.dmm is not None:
            self.dmm.write("MEAS:CURR:DC?")
            time.sleep(pyvisa.query_delay)
            current = float(self.dmm.read_raw(1024).decode("utf-8"))
            return current
            
        else:
            print("[DMM] No Device Connected")
            return False

    def get_resistance(self):
        """returns measured resistance in ohms

        Returns:
            bool: [description]
        """
        if self.dmm is not None:
            self.dmm.write("MEAS:RES?")
            time.sleep(pyvisa.query_delay)
            resistance = float(self.dmm.read_raw(1024).decode("utf-8"))
            return resistance
            
        else:
            print("[DMM] No Device Connected")
            return False
    
    def get_capacitance(self):
        """returns measured capacitance in farads

        Returns:
            bool: [description]
        """
        if self.dmm is not None:
            self.dmm.write("MEAS:CAP?")
            time.sleep(pyvisa.query_delay)
            capacitance = float(self.dmm.read_raw(1024).decode("utf-8"))
            return capacitance
            
        else:
            print("[DMM] No Device Connected")
            return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
    description="Handles adding device to devops repository"
    )
    parser.add_argument("--set-resolution", type=float, action="store", required=False, help="Resolution digits to sample in volts. (3.00e-04 * range) <= resolution <= (3.00e-07 * range)).")

    parser.add_argument("--set-range", type=float, action="store", required=False, help="Range of the DC volts measurement. Default is 1V [0.1V, 1V, 10V, 100V, 1000V].")

    parser.add_argument("--set-num-samples", type=int, action="store", required=False, help="Number of samples to take when reading. Default is 1.")

    parser.add_argument("--reset", action="store_true", required=False, help="Resets unit to factory settings.")

    parser.add_argument("--measure-voltage", action="store_true", required=False, help="Measures voltage reading.")

    parser.add_argument("--measure-current", action="store_true", required=False, help="Measures current reading.")

    parser.add_argument("--measure-resistance", action="store_true", required=False, help="Measures resistance reading.")

    parser.add_argument("--measure-capacitance", action="store_true", required=False, help="Measures capacitance reading.")

    args = parser.parse_args()
    
    dmm = DM3058E()

    if not dmm.online():
        print("Cannot find RIGOL DM3058E")
        sys.exit(1)
    
    if args.reset:
        print("Resetting DMM")
        dmm.reset()
    
    if args.set_num_samples is not None:
        num_samples = args.set_num_samples
        print("DMM set to take " + str(num_samples) + " sample(s)")
        dmm.set_num_samples(num_samples)
     
    if args.set_range is not None:
        range = args.set_range
        if range not in [.1, 1, 10, 100]:
            print("Invalid range. Must be in [.1, 1, 10, 100]")
            sys.exit(1)
        if not dmm.set_range_volts(range):
            print("Failed to set range")
            sys.exit(1)
        print("Range set to " + str(range) + "V")
    
    if args.set_resolution is not None:
        resolution = args.set_resolution
        _, _, range = dmm.get_configuration()
        if not (resolution >= (0.0000003 * range) and resolution <= (0.0003 * range)):
            print("Resolution is out of bounds. At " + str(range) + "V: [" + str(0.0000003 * range) + " <= resolution <= " + str(0.0003 * range) + "]")
            sys.exit(1)
        if not dmm.set_resolution(resolution):
            print("Failed to set resolution")
            sys.exit(1)
        print("Resolution set to " + str(resolution))
    
    if args.measure_voltage:
        voltage = dmm.get_voltage()
        if voltage:
            print("VOLTS: " + str(voltage))
        else:
            print("Failed to read voltage")
            sys.exit(1)
    
    if args.measure_current:
        current = dmm.get_current()
        if current:
            print("CURR: " + str(current))
        else:
            print("Failed to read current")
            sys.exit(1)
    
    if args.measure_resistance:
        resistance = dmm.get_resistance()
        if resistance:
            print("RES: " + str(resistance))
        else:
            print("Failed to read resistance")
            sys.exit(1)

    if args.measure_capacitance:
        capacitance = dmm.get_capacitance()
        if capacitance:
            print("CAP: " + str(capacitance))
        else:
            print("Failed to read capacitance")
            sys.exit(1)
