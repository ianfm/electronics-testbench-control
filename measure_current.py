from lib.keithley_2110 import Keithley2110 as DMM
import keyboard
import time
import sys
import signal
import argparse

maxCurrent = 0
avgCurrent = 0
sampleCount = 0

target_duration = None
target_samples = None
target_range = "10A"

parser = argparse.ArgumentParser(
  description="Controls the test to evaluate an EH mainboard"
)

# args.duration
parser.add_argument("--duration", type=str, action="store", required=False, help="Duration of continuous sampling (exclusive with num_samples)")

parser.add_argument("--num_samples", type=str, action="store", required=False, help="Number of samples to take (exclusive with duration)")

parser.add_argument("--cur_range", type=str, action="store", required=False, help="Current measurement range. One of [10mA, 100mA, 1A, 10A]. Default 10A")

args = parser.parse_args()

def end_measurement():
  global sampleCount
  global maxCurrent
  global avgCurrent
  print("Ending measurement\n")
  print(sampleCount,"samples were taken")
  print("MAX current was", maxCurrent, "A")
  print("AVG current was", avgCurrent, "A")


def signal_handler(sig, frame):
    end_measurement()
    # Exit the program
    sys.exit(0)

# Register the Ctrl+C signal handler
signal.signal(signal.SIGINT, signal_handler)

def get_current(dmm):
    """returns measured current in amps

    Returns:
        bool: [description]
    """
    global target_range
    
    if dmm is not None:
        # print("target range: ", target_range)
        cmd_string = "MEAS:CURR:DC? " + str(target_range)
        # print("cmd_string: ", cmd_string)
        dmm.dmm.write(cmd_string)
        time.sleep(0.05)
        current = float(dmm.dmm.read_raw(1024).decode("utf-8"))
        return current
        
    else:
        print("[DMM] No Device Connected")
        return False





def main():
  global target_duration
  global target_samples
  global target_range
  
  
  if args.cur_range != None:
    print("Set the current measurement range t0", args.cur_range)
    target_range = args.cur_range

  if args.duration != None:
    print("Sample for", args.duration, "seconds")
    target_duration = args.duration

  if args.num_samples != None:
    print("Take", args.num_samples, "samples")
    target_samples = args.num_samples
  
  try:
    dmm = DMM()
    global sampleCount
    global maxCurrent
    global avgCurrent

    if not dmm.online():
      print("DMM not found")
      return

    # Read until 'Q' is pressed``
    while True:
      current = get_current(dmm)
      sampleCount = sampleCount + 1
      if(current > maxCurrent):
        maxCurrent = current

      avgCurrent = avgCurrent + (current - avgCurrent) / sampleCount
      
      print(current)
      
      if target_samples != None:
        if sampleCount >= int(target_samples):
          end_measurement()
          return

      if keyboard.is_pressed('Q'):
        end_measurement()
        break

  except KeyboardInterrupt:
    pass


main()
