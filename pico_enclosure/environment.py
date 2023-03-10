"""Environment sensors."""

from time import sleep_ms, time  # pyright: ignore[reportGeneralTypeIssues]


from rp2 import PIO, asm_pio, StateMachine  # pyright: ignore[reportMissingImports]

from machine import SoftI2C, Pin  # pyright: ignore[reportMissingImports]
from micropython import const  # pyright: ignore[reportMissingImports]
import uasyncio as asyncio  # pyright: ignore[reportMissingImports]

from .api import api

CCS811_HARDWARE_ADDRS = (
    0x5A,
    0x5B,
)
"""The possible hardware address of the CCS811 sensor.

Sources:
    - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=4&zoom=100,96,177
"""

CCS811_DELAY: int = 100

# Registers
# Sources:
# - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=15&zoom=100,96,177
# - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=24&zoom=100,96,177
CCS811_STATUS_REG = (const(0x00), const(1))
"""Read only 1 byte register for sensor status."""
CCS811_MEAS_REG = (const(0x01), const(1))
"""Read/Write 1 byte register for sensor mode of operation"""
CCS811_ALG_REG = (const(0x02), const(8))
"""Read only 8 byte register containing algorithm results."""
CCS811_ENV_REG = (const(0x04), const(4))
"""Write only 4 byte register for storing temperature and humidity data for compensation."""
CCS811_ERROR_REG = (const(0xE0), const(1))
"""Read only 1 byte register containing the various error codes."""
CCS811_APP_START_REG = (const(0xF4), None)
"""Write only register to info the sensor to begin collecting data."""
CCS811_SW_RST_REG = (const(0xFF), const(4))
"""Software reset pin, puts the sensor into idle."""

DHTXX_EXPECTED_BITS = 40  # 2 bytes each temperature and humidity, 1 byte checksum


int_from_big_bytes = lambda v: int.from_bytes(v, "big")
"""Convert a bytearray into an integer."""


class CCS811:
    """I2C Gas Sensor for measuring VOCs and eCO2.

    Args:
        name: A unique name for the sensor
        sda: The SDA pin to which the sensor is connected
        scl: The SCL pin to which the sensor is connected
        mode: The starting mode of operation
        interrupt_pin: The GPIO Pin to which the interrupt Pin is connected

    """

    def __init__(
        self,
        name: str,
        sda: int,
        scl: int,
        mode: int = 1,
        interrupt_pin=None,
    ):
        self.name = name
        self._default_mode = mode
        self.interrupt_pin = interrupt_pin
        self.i2c_bus = SoftI2C(
            sda=Pin(sda),
            scl=Pin(scl),
            timeout=2000,
        )
        sleep_ms(CCS811_DELAY)
        for device in self.i2c_bus.scan():
            if device in CCS811_HARDWARE_ADDRS:
                self.device_addr = device
                print(f"Found device at {hex(device)}")
            break
        else:
            raise Exception("No CCS811 devices could be found")

        self.i2c_bus.writeto_mem(
            self.device_addr,
            CCS811_SW_RST_REG[0],
            b"\x11\xE5\x72\x8A",
        )
        sleep_ms(CCS811_DELAY)

        status = asyncio.run(self.status())
        if not status["error"] and status["app_valid"]:
            print(f"Device at {hex(self.device_addr)} ready.")
            self._start()
            asyncio.run(self.mode(self._default_mode))

        api.route(f"/{self.name}/mode")(self.mode)
        api.route(f"/{self.name}/status")(self.status)
        api.route(f"/{self.name}/data")(self.data)
        api.route(f"/{self.name}/error")(self.error)

    def _start(self):
        # https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=24&zoom=100,96,177
        self.i2c_bus.writeto_mem(
            self.device_addr,
            CCS811_APP_START_REG[0],
            b"",
        )
        sleep_ms(CCS811_DELAY)

    async def mode(self, mode=None):
        """Set the chip mode.

        Args:
            mode: The mode of operation to put the sensor in

        Returns:
            JSON representation containing the mode keys and values if mode is not given, echos the mode if given

        Notes:
            This implementation does not support threshold based interrupts on the sensor.

        Sources:
            - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=16&zoom=100,96,177

        """
        if mode is not None:
            mode = int(mode)
            if mode == 4:
                raise Exception("Mode 4 is not supported")
            byte = 0b0_111_0000 & mode << 4
            # byte &= 1 if self.interrupt_pin else 0 << 3
            self.i2c_bus.writeto_mem(
                self.device_addr,
                CCS811_MEAS_REG[0],
                byte.to_bytes(1, "big"),
            )
            asyncio.sleep_ms(CCS811_DELAY)
            return mode
        else:
            mode = int_from_big_bytes(
                self.i2c_bus.readfrom_mem(
                    self.device_addr,
                    *CCS811_MEAS_REG,
                ),
            )
            asyncio.sleep_ms(CCS811_DELAY)
            return {
                "drive_mode": mode >> 4,
                "interrupt_data_ready": bool(mode >> 3 & 1),
            }

    @staticmethod
    def _parse_status(status_byte):
        return {
            "fw_mode": bool(status_byte >> 7 & 1),
            "app_valid": bool(status_byte >> 4 & 1),
            "data_ready": bool(status_byte >> 3 & 1),
            "error": bool(status_byte & 1),
        }

    async def status(self):
        """Read the status of the CCS811.

        Returns:
            JSON containing the status of the sensor

        Sources:
            - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=16&zoom=100,96,177

        """
        status_byte = int_from_big_bytes(
            self.i2c_bus.readfrom_mem(
                self.device_addr,
                *CCS811_STATUS_REG,
            )
        )
        asyncio.sleep_ms(CCS811_DELAY)
        return self._parse_status(status_byte)

    async def data(self, status=False, error=False):
        """Get the algorithm results from the sensor.

        Args:
            status: Include the status response byte with the data
            error: Include the error response with the data

        Returns:
            JSON contain the sensor measurements.

        Sources:
            - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=18&zoom=100,96,177

        """
        data = self.i2c_bus.readfrom_mem(
            self.device_addr,
            *CCS811_ALG_REG,
        )

        res = {
            "eCO2": int_from_big_bytes(data[:2]),
            "TVOC": int_from_big_bytes(data[2:4]),
        }

        if status or status == "true":
            res.update(self._parse_status(data[4]))

        if error or error == "true":
            res.update(self._parse_error(data[5]))

        asyncio.sleep_ms(CCS811_DELAY)

        return res

    def _parse_error(self, error_byte):
        return {
            "WRITE_REG_INVALID": bool(error_byte & 1),
            "READ_REG_INVALID": bool(error_byte >> 1 & 1),
            "MEASMODE_INVALID": bool(error_byte >> 2 & 1),
            "MAX_RESISTANCE": bool(error_byte >> 3 & 1),
            "HEATER_FAULT": bool(error_byte >> 4 & 1),
            "HEATER_SUPPLY": bool(error_byte >> 5 & 1),
        }

    async def error(self):
        """Get the error state of the sensor.

        Returns:
            JSON contain the various error states

        Sources:
            - https://cdn-shop.adafruit.com/product-files/3566/3566_datasheet.pdf#page=22&zoom=100,96,177

        """
        data = int_from_big_bytes(
            self.i2c_bus.readfrom_mem(
                self.device_addr,
                *CCS811_ERROR_REG,
            )
        )
        return self._parse_error(data)


_DHTXX_SM_CLOCK_FREQ = 500000  # 1 / 500Khz = 2us cycle


@asm_pio(
    set_init=PIO.OUT_HIGH,  # Interaction Pin should be set to HIGH
    autopush=True,  # Automatically push data onto the FIFO queue
    push_thresh=8,  # Push when there are 8 available bits
)
def _DHTXX_PIO_ASM():
    """State machine assembly for the Pico to interface with a DHTXX sensor.

    Sources:
        - https://github.com/danjperron/PicoDHT22/blob/main/PicoDHT22.py
        - https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf#section_pio
        - https://www.i-programmer.info/programming/hardware/14572-the-pico-in-micropython-a-pio-driver-for-the-dht22.html?start=1

    """
    # 1. ---- Pull the pin low for > 1ms(DHT22, AM2302)|18ms(DHT11)
    # 2. ---- Pull the pin high for 20-40us
    # 3. ---- Sensor pulls low for 80us
    # 4. ---- Sensor pulls high for 80us
    # 5. ---- Sensor pulls low for 50us to indicate start of bit pulse
    # 6. ---- Wait 35-40us and read pin for bit designation
    # 7. ---- Repeat
    pull()  #               1 - (1) Pull input from OSR
    mov(x, osr)  #          2 - (1) Move our low time value into x
    set(pindirs, 1)  #      3 - (1) Set pin to output
    set(pins, 0)  #         4 - (1) Drive pin low
    label("wait")  #        5 - (1)
    jmp(x_dec, "wait")  #   6 - (1) loop if non-zero, post decrement
    set(pins, 1)[19]  #     7 - (2) Drive pin high, delay for 19 cycles, 40us total
    set(pindirs, 0)  #      8 - (3) Set pin to input
    wait(1, pin, 0)  #      9 - (4) Wait for base pin to go high
    wait(0, pin, 0)  #     10 - (5) Wait for base pin to go low
    label("bit_loop")  #   11 - (7)
    wait(1, pin, 0)[17]  # 12 - (6) Wait for base pin go high, delay 18 cycles (36us)
    in_(pins, 1)  #        13 - (6) Write current pin state to ISR
    wait(0, pin, 0)  #     14 - (6) Wait for pin to go low
    jmp("bit_loop")  #     15 - (7) Return to start of the bit loop


class DHTXX:
    """Family of temperature and humidity sensors.

    Args:
        name: A name for the sensor
        pin: The data pin of the sensor
        rest_time: How long to rest in between taking samples. This value is sensor-model dependent
        initial_low_pulse_duration: How long to pull low for initially. This value is sensor-model dependent
        state_machine_id: An Id of the state machine to use
        unit: The unit of temperature to return. Supports 'celsius' and 'fahrenheit'.

    """

    def __init__(
        self,
        name,
        pin,
        rest_time: int,
        initial_low_pulse_duration: int,
        state_machine_id: int = 0,
        unit: str = "celsius",
    ) -> None:
        self.name = name
        self._rest_time = rest_time
        self._initial_low_pulse_duration = round(
            initial_low_pulse_duration
            / 1000
            * _DHTXX_SM_CLOCK_FREQ  # Convert a ms pulse duration to cycles
        )

        self._data_pin = Pin(pin)

        self._state_machine = StateMachine(state_machine_id)

        self._temp = None
        self._humidity = None

        self._last_measurement_time = 0
        self._unit = unit

        # TODO manage using multiple state machines automatically
        PIO(state_machine_id).remove_program()

        self._state_machine.init(
            _DHTXX_PIO_ASM,
            freq=_DHTXX_SM_CLOCK_FREQ,
            set_base=self._data_pin,
            in_base=self._data_pin,
            jmp_pin=self._data_pin,
        )

        api.route(f"/{self.name}/data")(self.data)

    async def data(self):
        """Use the sensor to take a measurement if one is available.

        A new measurement will only be allowed every ``self._rest_time`` seconds.

        Returns:
            JSON containing the temperature and humidity readings.

        """
        current_time = time()
        if current_time - self._last_measurement_time > self._rest_time:
            self._state_machine.put(
                self._initial_low_pulse_duration
            )  # Wait for at least 2ms
            self._state_machine.active(1)

            bytes_ = [self._state_machine.get() for _ in range(5)]
            self._state_machine.active(0)
            humidity = bytes_[0] << 8 | bytes_[1]
            raw_temp = bytes_[2] << 8 | bytes_[3]

            #  Checksum should be the last 8 bits of the sum of each byte in the temperature and humidity values
            # Example:
            # Checksum = 152 (1001_1000)
            # >> [0000_0001, 1011_0101, 0000_0000, 1110_0010, 1001_1000]
            # >> [        1,       181,         0,       226,       152]
            # >> 1 + 181 + 0 + 226 = 408 = 1_1001_1000
            # >> 408 & 0xFF = 152
            if (
                bytes_[4]
                != (
                    (humidity >> 8)
                    + (humidity & 255)
                    + (raw_temp >> 8)
                    + (raw_temp & 255)
                )
                & 0xFF
            ):
                raise Exception("Checksum validation failed.")

            self._humidity = humidity / 10
            # First bit is sign, remaining 15 are temperature
            self._temp = (
                (1 if not raw_temp >> 15 else -1) * (raw_temp & (2**15 - 1)) / 10
            )

            if self._unit == "fahrenheit":
                self._temp = round(self._temp * 9 / 5 + 32, 1)

        return {
            "temperature": self._temp,
            "humidity": self._humidity,
        }


class DHT11(DHTXX):
    """A smaller, cheaper, higher sampling and less accurate temperature and humidity sensor.

    Args:
        name: A name for the DHT11 sensor
        pin: The data pin of the DHT11 sensor

    Sources:
        - https://cdn-shop.adafruit.com/datasheets/DHT11-chinese.pdf

    """

    def __init__(self, name, pin, unit):
        super().__init__(
            name,
            pin,
            rest_time=1,  # 1Hz sampling
            initial_low_pulse_duration=20,  # 20ms initial low pulse
            unit=unit,
        )


class DHT22(DHTXX):
    """A larger, more expensive, slow sampling and more accurate temperature and humidity sensor.

    Args:
        name: A name for the DHT22/AM2302 sensor
        pin: The data pin for the DHT22/AM2302 sensor

    Sources:
        - https://www.sparkfun.com/datasheets/Sensors/Temperature/DHT22.pdf
        - https://cdn-shop.adafruit.com/datasheets/Digital+humidity+and+temperature+sensor+AM2302.pdf

    """

    def __init__(self, name, pin, unit):
        super().__init__(
            name,
            pin,
            rest_time=2,
            initial_low_pulse_duration=2,
            unit=unit,
        )
