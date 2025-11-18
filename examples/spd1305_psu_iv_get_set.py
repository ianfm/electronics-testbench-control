from testbench.psu.siglent_spd1305x import SiglentSPD1305X
from testbench.core.scpi import SCPISettings
from time import sleep

# Optional: restrict to USB resources
settings = SCPISettings(resource_filter="USB?*")
psu = SiglentSPD1305X(settings=settings)

# Set voltage and current
psu.set_current(1, 0.5)
psu.set_voltage(1, 0.7)
sleep(1)
psu.get_current(1)
psu.get_voltage(1)

# Troubleshooting
# psu.driver.resource.write("CH1:CURR?")
# psu.driver.resource.read_raw()