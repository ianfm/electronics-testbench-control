# Argparse command test
#TODO need pauses between commands

# Test channel, voltage, and current setters
python ..\siglent_SPD1305X.py --set-channel 1 --set-voltage 3.0 --set-current 0.003
python ..\siglent_SPD1305X.py --set-channel 1 --set-voltage 3.3 --set-current 0.010

# Test display feature
python ..\siglent_SPD1305X.py --display-on
python ..\siglent_SPD1305X.py --display-off

