from time import sleep

from machine import Pin

from pico_enclosure.network import Network

led = Pin("LED", Pin.IN)
led.value(1)
try:
    Network()
    sleep(10)
finally:
    led.value(0)
