# rigol dmm argparse command test

## Power
python .\rigol_DM3058E.py --reset
sleep(5)

## Measurement capture suite
python .\rigol_DM3058E.py --measure-voltage
sleep(2)
python .\rigol_DM3058E.py --measure-current
sleep(2)
python .\rigol_DM3058E.py --measure-resistance
sleep(2)
python .\rigol_DM3058E.py --measure-capacitance
sleep(2)


## TODO: Failing the setting conf suite
## Adjust settings
# python .\rigol_DM3058E.py --set-resolution
# sleep(2)
# python .\rigol_DM3058E.py --set-range 0.1V
# sleep(2)
# python .\rigol_DM3058E.py --set-num-samples 3
# sleep(2)

