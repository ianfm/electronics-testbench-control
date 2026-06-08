# Taken from https://info.erdosmiller.com/blog/the-scpi-command-interface-ring-out-testing-with-the-rigol-m300


# Part 1
# ====================================================================
from future_ import print_function
import numpy as np  
import pandas as pd  
import visa  
import time  
import logging  
import sys  
import os

# reference to each wire and its position in the matrix.Call by wirenumber-1.
wire_list = ["111", "112", "113" "114", "115", "116", "117", "118", 
             "211", "212", "213", "214", "311", "312", "313", "314", 
             "315", "316", "317", "318", "215", "216", "217", "218"]

if not os.path.isdir("./logs"):
    os.makedirs("./logs")
    
logFilename = "./logs/ErrorLog-txt"
logging.basicConfig(filename=logFilename, level=logging.WARNING)

dfmaster = pd.read_csv('mastervalues.csv', index_col=False, header=None)

M300, KS34465A, tool_serial = "", "", input("What is the tool serial number? \n")

Filename = "./logs/%s_RO_Values_%s.csv" % (tool_serial, time.strftime("%Y%m%d-%H%M") )

rm = visa.ResourceManager()
# Get the USB devices, e.g.'USBO: :0x1AB1: :0x0588: :DS1ED141904883*
usb = list(filter(lambda x: "USB" in x, rm.list_resources()))



# OCR fixup Part 2
# ====================================================================

def OpenComs () :
    global M300
    global KS34465A
    for address in usb: # Identifies the M300 and Keysight Multimeter.We know their IDs already
        if address.find('MM3A161750031') != -1:
            M300 = rm.open_resource(address)
        elif address.find('MY54507559') != -1:
            KS34465A = rm.open_resource(address)
    
def CloseComs():
    global M300
    global KS34465A
    M300.close()
    KS34465A.close()


# OCR fixup Part 3
# ====================================================================

def MakeMeasurement(x, y, delay, setting, rerun):
    OpenComs()
    M300.write("ROUT: CLOS (@%s,%s,401,410)" % (wire_list[y], wire_list[x]))
    
    measurement = round(np.median(
        list(map(float, KS34465A.query("MEAS:FRES? %s" % setting).split(',')))))
    
    M300.write("ROUT: OPEN (@%s,%s,401,410)" % (wire_list[y], wire_list[x]))
    
    time.sleep(delay)
    CloseComs()
    
    measurement = 9999999 if measurement >= 950000 else measurement
    measurement = 0 if measurement <= 20 and measurement >= 0 else measurement
    
    if x == 11 and rerun == False:
        print("%d" % measurement, end="")
        sys.stdout.flush()
    elif not x == 11 or rerun == True:
        print("%d, " % measurement, end="")
        sys.stdout.flush()
    return int(measurement)



# OCR fixup Part 4
# ====================================================================

def Main():
    the_matrix = []
    for Y in range (12, 24):
        rowList = [] 
        print ("[", end="") 
        sys.stdout.flush()
        for X in range(0, 12):
            rowList.append(MakeMeasurement(X, Y, .03, 'AUTO', False))
        print ("]")
        the_matrix.append (rowList)
    dfMeasure = pd.DataFrame(the_matrix)
    # Check against the master values and rerun the measurments if not within specs
    for Y in range(12, 24): 
        for X in range(0, 12):
            if dfMeasure[X][Y-12] > dfmaster[X][Y-12]*1.15 or dfMeasure[X][Y-12] < dfmaster[X][Y-12]*.85:
                print ("Remeasuring Pin %s, Socket %s: " %
                (X+1, Y-11), end="")
                sys.stdout.flush()
                dfMeasure.at[Y-12, X] = round(np.median ([MakeMeasurement(X, Y, .03, 'AUTO', True) for i in range(
                5)]))
    print ("")

    dfMeasure.to_csv(
        ("filename"), 
        sep=",",
        index=False,
        header=False,
    )



# OCR fixup Part 5
# ====================================================================

try:
    start = time.time()
    OpenComs ( )
    M300.write('*RST')
    KS34465A.write('*RST')
    print (M300.query('*IDN?'))
    print (KS34465A.query('*IDN?'))
    Main()
    print(" Done!" + "\n Time taken: " + str(time.time() - start))
    logging.shutdown ( )
    
except Exception as e:
    logging.exception ("Got exception on main handler: " + str(e))
    CloseComs()
    raise

