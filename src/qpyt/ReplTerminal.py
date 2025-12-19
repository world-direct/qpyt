
import logging
import sys
import threading
import time

from qpyt.ComPort import ComPort

log = logging.getLogger(__name__)

class ReplTerminal:
    """
    Class to handle interaction with the QuecPython REPL over serial port
    Allows to send commands and receive output
    """

    PROMPT = b"\r\n>>> "

    def __init__(self, port):

        # port handling
        self.port = ComPort(port, self.on_data)
        self.port.test_port()

        # command support variables
        self._command_mode = False
        self._command_event = None
        self._command_response = b''

        self.is_busy = True
        self.enable_print = True

        self.port.start()

    def close(self):
        self.port.close()

    def on_data(self, data: bytes):
        if len(data) == 0:
            return

        if self.enable_print:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

        # if we are not in command mode, ignore further processing
        if not self._command_mode:
            return

        # until we get the full prompt, accumulate data for command-response
        self._command_response += data

        # check if _response ends with the prompt
        if self._command_response.endswith(self.PROMPT):
            log.debug("Detected REPL prompt while waiting for command")
            # remove the prompt from the end for the response
            self._command_response = self._command_response[:-len(self.PROMPT)]

            # set event to main thread continues
            self._command_mode = False
            self._command_event.set()
            return

    def execute_command(self, command:str, timeout:float=105.0):
        """Write a command to the serial port and wait for prompt"""
        log.debug(f"Writing command: {command}")

        if self._command_mode:
            raise RuntimeError("Already in command mode")
        
        if self._command_event is not None:
            raise RuntimeError("Previous command still pending")

        self.enable_print = False
        self._command_mode = True
        self._command_event = threading.Event()
        self._command_response = b''

        if not command.endswith("\r\n"):
            command += "\r\n"

        self.port.write(command.encode("utf-8"))
        if not self._command_event.wait(timeout):
            raise TimeoutError(f"Timeout waiting for command response: {command}")

        self.enable_print = True
        response = self._command_response.decode("utf-8", errors="ignore")
        self._command_event = None

        # strip command echo from the start of the response
        if response.startswith(command):
            response = response[len(command) :]

        self.command_response = None
        self._command_event = None
        log.debug(f"Command response: {repr(response)}")
        return response

    def soft_reset(self):
        """Soft reboot the board"""
        self.port.write(b"\x04")  # Send Ctrl+D
        self.port.write(b"\r\n")
        self.is_busy = True

    def ensure_ready(self):
        """Ensure that the board is in REPL mode"""
        if self.is_busy:
            self.interrupt()
            self.is_busy = False

    def interrupt(self, attempts=3):
        """
        Interrupt running QuecPython program and return to REPL
        Sends Ctrl+C (ASCII 3) multiple times
        """
        log.debug("Interrupting running program...")

        self.enable_print = False
        for i in range(attempts):
            self.port.write(b"\x03")  # Send Ctrl+C
            time.sleep(0.1)

        # execute empty command to get fresh REPL prompt
        res = self.execute_command("print('READY')")
        if res == "READY":
            log.debug("Successfully interrupted to REPL")
        else:
            log.warning("Failed to interrupt to REPL, unexpected response: %s", repr(res))
            exit(1)

        log.debug("Program interrupted, returned to REPL")
