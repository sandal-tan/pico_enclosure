import uasyncio as asyncio
from time import sleep

from machine import Pin
from pico_enclosure.devices import Devices

led = Pin("LED", Pin.IN)
led.value(1)
try:
    devices = Devices()
    strip = devices["lights"]

    for var in [
        (255, 0, 0),  # red
        (0, 255, 0),  # green
        (0, 0, 255),  # blue
    ]:
        asyncio.run(strip.fill(*var))
        sleep(1)

    asyncio.run(strip.fill(255, 255, 255, 0.1))
    sleep(1)
    asyncio.run(strip.off())
    sleep(1)
    asyncio.run(strip.on())
finally:
    led.value(0)
