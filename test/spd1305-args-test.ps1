# Argparse command test

# Test channel, voltage, and current setters
python ..\siglent_SPD1305X.py --set-channel 1 --set-voltage 3.0 --set-current 0.003
sleep(1)
python ..\siglent_SPD1305X.py --set-channel 1 --set-voltage 3.3 --set-current 0.010
sleep(1)

# Test display feature
python ..\siglent_SPD1305X.py --display-on
sleep(1)
python ..\siglent_SPD1305X.py --display-off


