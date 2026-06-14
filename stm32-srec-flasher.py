#!/usr/bin/env python3
import argparse
import os
import sys
import time
import serial


class BootloaderFlasher:

    def __init__(self, port, srec_path, baudrate=115200):
        self.port = port
        self.srec_path = srec_path
        self.baudrate = baudrate
        self.ser = None

    def log_state(self, message):
        """Standardized state logging format."""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] INFO: {message}")

    def log_error(self, message):
        print(f"\n[-] ERROR: {message}", file=sys.stderr)

    def run(self):
        # Verify the SREC file exists before opening the serial port
        if not os.path.isfile(self.srec_path):
            self.log_error(f"SREC file not found at path: {self.srec_path}")
            sys.exit(1)

        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,  # 100ms read timeout
            )
        except serial.SerialException as e:
            self.log_error(f"Could not open serial port {self.port}: {e}")
            sys.exit(1)

        self.log_state(f"Connected to {self.port} at {self.baudrate} baud.")
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        try:
            # ---- STEP 1: INITIAL PASSIVE MONITORING (3 SECONDS) ----
            self.log_state(
                "Monitoring port for existing 'booting' stream (3s window)..."
            )
            detected_early_boot = self._wait_for_string(
                "booting", timeout_sec=3.0
            )

            if detected_early_boot:
                self.log_state("Early 'booting' signal detected on channel!")
            else:
                self.log_state(
                    "No active booting signal heard. Initiating reset sequence..."
                )

                # ---- STEP 2: SEND RESET COMMAND ----
                self.log_state("Sending 'reset' command...")
                self.ser.write(b"reset\n")
                self.ser.flush()

                # ---- STEP 3: WAIT FOR RESET ACK ----
                self.log_state("Waiting for 'reset ack'...")
                if not self._wait_for_string("reset ack", timeout_sec=2.0):
                    self.log_error("Timeout waiting for 'reset ack'")
                    return False
                self.log_state("Reset Successful")

                # ---- STEP 4: POST-RESET BOOTING CAPTURE ----
                self.log_state("Waiting for post-reset 'booting' frame...")
                if not self._wait_for_string("booting", timeout_sec=3.0):
                    self.log_error("Timeout waiting for 'booting' sequence")
                    return False

            # ---- STEP 5: SEND ENTER BOOTLOADER ----
            self.ser.write(b"enter bootloader\n")
            self.ser.flush()
            self.log_state("Entering Bootloader...")

            # ---- STEP 6: VERIFY ENTRANCE ACK ----
            if not self._wait_for_string(
                "enter bootloader ack", timeout_sec=2.0
            ):
                self.log_error("Timeout waiting for 'enter bootloader ack'")
                return False
            self.log_state("Bootloader connection acknowledged!")

            # ---- STEP 7: STREAM SREC DATA ----
            return self._stream_srec_file()

        finally:
            self.ser.close()
            self.log_state("Serial port closed cleanly.")

    def _wait_for_string(self, target_str, timeout_sec):
        """Helper to search for target tokens within a timeframe."""
        start_time = time.time()
        buffer = ""
        while (time.time() - start_time) < timeout_sec:
            if self.ser.in_waiting > 0:
                raw_chars = self.ser.read(self.ser.in_waiting).decode(
                    "utf-8", errors="ignore"
                )
                buffer += raw_chars
                if target_str in buffer:
                    return True
            time.sleep(0.005)
        return False

    def _stream_srec_file(self):
        """Opens and parses the SREC file, streaming it line by line."""
        self.log_state(f"Opening SREC file: {self.srec_path}")

        with open(self.srec_path, "r") as file:
            lines = file.readlines()

        total_lines = len(lines)
        self.log_state(f"Loaded {total_lines} SREC lines. Beginning transmission...")

        # Clear out any trailing handshake bytes so the buffer contains only ticks
        self.ser.reset_input_buffer()

        print("[ Flashing progress: ", end="", flush=True)

        for idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Format line payload with expected LF line termination character
            payload = (line + "\n").encode("utf-8")

            # Send the line out to the serial port
            self.ser.write(payload)
            self.ser.flush()

            # MANDATORY: Wait for a tick back for EVERY line (S0, S1, S2, S3, S7, S8, S9)
            if not self._wait_for_tick(timeout_sec=40.0):
                print(" ]")  # Close progress brackets on error
                self.log_error(f"Target stopped responding at SREC line {idx+1}/{total_lines} ({line[:2]})")
                return False

        print(" ]")  # Completed successfully

        # ---- MONITOR FINAL REBOOT SIGNAL ----
        self.log_state("Waiting for termination record completion message...")
        if self._wait_for_string("FLASH_SUCCESS", timeout_sec=5.0):
            print("\n[+] SUCCESS: Firmware flashed completely. Microcontroller is running the application!\n")
            return True
        else:
            self.log_error("Flashing ended without confirming success token.")
            return False

    def _wait_for_tick(self, timeout_sec):
        """Waits for the bootloader's custom character token validation string."""
        start_time = time.time()
        while (time.time() - start_time) < timeout_sec:
            if self.ser.in_waiting > 0:
                char = self.ser.read(1).decode("utf-8", errors="ignore")
                if char == ".":
                    print(".", end="", flush=True)  # Echo to terminal window
                    return True
            time.sleep(0.001)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="STM32 SREC Automated Flasher Utility"
    )
    parser.add_argument(
        "port", help="Target serial port device path (e.g. /dev/ttyACM0)"
    )
    parser.add_argument("srec", help="Path to your application .srec file")
    parser.add_argument(
        "-b", "--baud", type=int, default=115200, help="Baudrate (default: 115200)"
    )

    args = parser.parse_args()

    flasher = BootloaderFlasher(
        port=args.port, srec_path=args.srec, baudrate=args.baud
    )
    success = flasher.run()

    if not success:
        sys.exit(1)
