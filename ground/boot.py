import usb_cdc

# Enable both the REPL (console) and the data serial port.
# This creates two CDC devices:
#   /dev/ttyACM0  → REPL / console
#   /dev/ttyACM1  → data port (used by cli.py and ground/main.py)
usb_cdc.enable(console=True, data=True)
