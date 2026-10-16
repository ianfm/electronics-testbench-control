from lib.keithley_2110 import Keithley2110 as DMM
import keyboard
import time
import sys
import signal
import argparse
import csv

maxVoltage = 0
minVoltage = 1000 # Hack: this is higher than the max voltage of the keithley dmm
avgVoltage = 0
sampleCount = 0

target_duration = None
target_samples = None
target_range = None
start_time = None

dmm = None

# Create a list to store readings
readings = []

parser = argparse.ArgumentParser(
  description="Controls the test to evaluate an EH mainboard"
)

# args.duration
parser.add_argument("--free_run", type=bool, action="store", required=False, default=False, help="Sample continuously until stopped by user. Overrides other args")

parser.add_argument("--duration", type=float, action="store", required=False, help="Duration of continuous sampling (exclusive with num_samples)")

parser.add_argument("--num_samples", type=int, action="store", required=False, help="Number of samples to take (exclusive with duration)")

parser.add_argument("--volt_range", type=str, action="store", required=False, choices=['100mV', '1V', '10V', '100V', '1000V'], default='10V', help="Voltage measurement range. Default 10V")

parser.add_argument("--filename", type=str, action="store", required=False, help="Basename of CSV file to save measurements to")
args = parser.parse_args()

def end_measurement():
  global sampleCount
  global maxVoltage
  global minVoltage
  global avgVoltage
  
  global start_time
  
  max_string = f"{maxVoltage:.{5}f}"
  min_string = f"{minVoltage:.{5}f}"
  avg_string = f"{avgVoltage:.{5}f}"
  duration = time.time() - start_time
  
  print("Ending measurement\n")
  print(sampleCount,"samples were taken over", duration, "seconds")
  print("MAX voltage was", max_string, "V")
  print("MIN voltage was", min_string, "V")
  print("AVG voltage was", avg_string, "V")
  dmm.dmm.close()
  

def signal_handler(sig, frame):
  end_measurement()
  # Exit the program
  sys.exit(0)

# Register the Ctrl+C signal handler
signal.signal(signal.SIGINT, signal_handler)

def get_voltage(dmm):
  """returns measured voltage in volts

  Returns:
      bool: [description]
  """
  global target_range
  
  if dmm is not None:
      # print("target range: ", target_range)
      cmd_string = "MEAS:VOLT:DC? " + str(target_range)
      # print("cmd_string: ", cmd_string)
      dmm.dmm.write(cmd_string)
      time.sleep(0.05)
      voltage = float(dmm.dmm.read_raw(1024).decode("utf-8"))
      return voltage
      
  else:
      print("[DMM] No Device Connected")
      return False





def main():
  global target_duration
  global target_samples
  global target_range
  
  global sampleCount
  global maxVoltage
  global minVoltage
  global avgVoltage
  
  global start_time
  
  # Save each reading to a CSV file
  file_prefix = ""
  file_suffix = ""
  filename = str(str(args.filename) + ".csv")

  if args.volt_range != None:
    print("Set the voltage measurement range t0", args.volt_range)
    target_range = args.volt_range

  if args.duration != None:
    print("Sample for", args.duration, "seconds")
    target_duration = args.duration

  if args.num_samples != None:
    print("Take", args.num_samples, "samples")
    target_samples = args.num_samples
    
  # Free running mode will measure until the program is stopped
  if args.free_run != False:
    target_duration = None
    target_samples = None
  
  try:
    global dmm
    dmm = DMM()

    if not dmm.online():
      print("DMM not found")
      return
    
    start_time = time.time()

    # Read until 'Q' is pressed``
    while True:
      voltage = get_voltage(dmm)
      sampleCount = sampleCount + 1
      if(voltage > maxVoltage):
        maxVoltage = voltage
      elif(voltage < minVoltage):
        minVoltage = voltage

      avgVoltage = avgVoltage + (voltage - avgVoltage) / sampleCount
      
      print(voltage)
      
      if args.filename != None and args.free_run == False:
        with open(filename, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([voltage, time.time()])
      
      if target_samples != None:
        if sampleCount >= int(target_samples):
          end_measurement()
          return

      if target_duration != None:
        cur_time = time.time()
        if (cur_time - start_time) >= float(target_duration):
          # print("start time:", start_time)
          # print("voltage time:", cur_time)
          # print("difference:", cur_time - start_time)
          # print("target duration:", target_duration)
          end_measurement()
          return

      if keyboard.is_pressed('Q'):
        end_measurement()
        break

  except KeyboardInterrupt:
    pass


main()
