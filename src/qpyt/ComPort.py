import logging
import threading
import time

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)


class ComPort:
    """Class to handle serial port communication. It also handles reestablishing of the connection."""

    def __init__(self, port, on_data: callable, on_reconnect: callable = None):
        self.on_data = on_data
        self.on_reconnect = on_reconnect
        self.port_uri = ComPort.find_serial_port(port)

    def test_port(self):
        try:
            # test open port
            log.debug(f"Testing serial port {self.port_uri}")
            test_open = serial.serial_for_url(self.port_uri)
            test_open.close()
            log.debug(f"Sucessfully opened and closed port {self.port_uri}")
        except Exception as e:
            log.error(f"Failed to open serial port {self.port_uri}: {e}")
            exit(1)

    def start(self):
        self._create_serial()
        # Start background reader
        self.stop_event = threading.Event()
        self._reader_thread = threading.Thread(target=self.serial_reader, daemon=True)
        self._reader_thread.start()

    def _create_serial(self):
        log.debug(f"Opening serial port {self.port_uri}")
        self._ser = serial.serial_for_url(self.port_uri)
        log.debug(f"Serial port {self.port_uri} opened")
        self._ser.timeout = 1

    def close(self):
        self.stop_event.set()
        self._reader_thread.join()
        self._ser.close()

    def serial_reader(self):
        """Background thread to read from serial and write to stdout"""
        while not self.stop_event.is_set():
            try:
                if self._ser.in_waiting > 0:
                    data = self._ser.read(self._ser.in_waiting)
                    if len(data) == 0:
                        continue

                    log.debug("<--" + repr(data))

                    # callback
                    self.on_data and self.on_data(data)

                else:
                    # Small sleep to prevent CPU spinning
                    time.sleep(0.01)
            except serial.SerialException as se:
                log.warning(f"Serial disconnected: {se}")
                log.info("Attempting to reconnect...")
                try:
                    self._create_serial()
                    log.info("Reconnected to serial port.")
                    if self.on_reconnect:
                        self.on_reconnect()
                    continue
                except Exception as e:
                    log.warning(f"Failed to reopen serial port {self.port_uri}: {e}")
                    time.sleep(1)

    def write(self, data: bytes):
        """Write data to serial port"""
        log.debug("-->" + repr(data))
        self._ser.write(data)

    @staticmethod
    def find_serial_port(port: str):
        """Try to find the serial port by name or description"""

        # rfc2217://localhost:1111
        if port.startswith("rfc2217://"):
            log.info("Using RFC2217 port: %s", port)
            return port

        # check if it is just HOST:PORT
        if ":" in port and not port.startswith("/"):
            uri = "socket://" + port
            log.info("Using port: %s", uri)
            return uri

        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            if p.name == port:
                # port port is specifed directly, use it
                log.info("Using specified port: %s", p.name)
                return p.name

            if p.description.find(port) != -1:
                # found matching port
                log.info("Auto-detected device on port: %s", p.name)
                return p.name

        raise RuntimeError(f"Could not find serial port matching: {port}")
