from machine import UART
import time

# UART2 on ESP32
uart = UART(2, baudrate=115200, tx=17, rx=16)

print("ESP32 UART ready")

while True:
    
    if uart.any():
        data = uart.readline()
        
        if data:
            msg = data.decode().strip()
            print("Received:", msg)

            reply = "Echo from ESP32: " + msg + "\n"
            uart.write(reply)

    time.sleep(0.1)