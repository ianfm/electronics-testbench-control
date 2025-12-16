from __future__ import annotations

import argparse
import csv
import sys
import time

from testbench.core.scpi import SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B

smu = KeysightB2902B(resource_name="USB0::10893::37377::MY60440156::0::INSTR")

NPTS = 86

smu.driver.write("*RST")
smu.driver.write("*CLS")
smu.driver.write(":INST:NSEL 1")

# --- Source sweep setup ---
smu.driver.write(":SOUR:FUNC VOLT")
smu.driver.write(":SOUR:VOLT:MODE SWE")
smu.driver.write(":SOUR:SWE:SPAC LIN")
smu.driver.write(":SOUR:VOLT:STAR 0")
smu.driver.write(":SOUR:VOLT:STOP 8.5")
smu.driver.write(f":SOUR:VOLT:POIN {NPTS}")

# --- Backdrive prevention (asymmetric protection) ---
smu.driver.write(":SENS:CURR:PROT:POS 1e-9")     # or 1e-6 if needed
smu.driver.write(":SENS:CURR:PROT:NEG 0.6")

# --- Measurement config ---
smu.driver.write(':SENS:FUNC "VOLT"')
smu.driver.write(':SENS:FUNC "CURR"')
smu.driver.write(":SENS:VOLT:NPLC 0.2")
smu.driver.write(":SENS:CURR:NPLC 0.2")
smu.driver.write("SENS1:WAIT:OFFS 0.005")

# --- TRACE: must size buffer while FEED:CONT is NEV ---
smu.driver.write(":TRAC:CLE")
smu.driver.write(":TRAC:FEED:CONT NEV")       # allow changing buffer size :contentReference[oaicite:3]{index=3}
smu.driver.write(f":TRAC:POIN {NPTS}")        # set buffer capacity :contentReference[oaicite:4]{index=4}
smu.driver.write(":TRAC:FEED SENS")
smu.driver.write(":FORM:ELEM VOLT,CURR")
smu.driver.write(":TRAC:FEED:CONT NEXT")      # start logging; stops when full :contentReference[oaicite:5]{index=5}

# --- TRIGGER: make sure acquire/transient counts match sweep points ---
smu.driver.write(f":TRIG:TRAN:COUN {NPTS}")   # source steps :contentReference[oaicite:6]{index=6}
smu.driver.write(f":TRIG:ACQ:COUN {NPTS}")    # measurements :contentReference[oaicite:7]{index=7}
smu.driver.write(":TRIG:SOUR IMM")

# --- Run ---
smu.driver.write(":OUTP ON")
smu.driver.write(":INIT")
smu.driver.query_raw("*OPC?")

# --- Verify and fetch exactly what was stored ---
act = int(float(smu.driver.query_raw(":TRAC:POIN:ACT?")))   # should be NPTS :contentReference[oaicite:8]{index=8}
smu.driver.query_raw(f":TRAC:DATA? 1,{act}")         # V1,I1,V2,I2,...
