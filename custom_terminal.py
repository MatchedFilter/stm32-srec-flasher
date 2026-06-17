#!/usr/bin/env python3
import argparse
import sys
import os
import time
import threading
import serial


class CustomTerminal:
    def __init__(self, port, baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.running = True
        self.reconnecting = False
        
        # Timing flag to track when the prompt needs a re-draw
        self.last_activity_time = time.time()
        self.prompt_needs_redraw = True

    def log_error(self, message):
        sys.stdout.write(f"\n[-] ERROR: {message}\n")
        sys.stdout.flush()

    def log_info(self, message):
        sys.stdout.write(f"\n[+] {message}\n")
        sys.stdout.flush()

    def _open_port(self, timeout_limit=10.0):
        """Attempts to physically open the serial port file within a time window."""
        start_time = time.time()
        while (time.time() - start_time) < timeout_limit and self.running:
            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.1,
                )
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                return True
            except (serial.SerialException, OSError):
                time.sleep(0.2)
        return False

    def _handle_reconnect(self):
        """Coordinates a single safe fallback sequence across all active loops."""
        # Double-check flag to prevent multiple threads from invoking recovery simultaneously
        if self.reconnecting:
            return
        
        self.reconnecting = True
        self.log_info("Connection dropped. Attempting automatic reconnection...")
        
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

        # Give the Host OS a moment to clean up file handle structures
        time.sleep(0.5)

        if self._open_port(timeout_limit=10.0):
            self.log_info(f"Reconnected successfully to {self.port}!")
            self.last_activity_time = time.time()
            self.prompt_needs_redraw = True
            self.reconnecting = False
        else:
            self.log_error(f"Failed to reconnect to {self.port} within 10 seconds.")
            self.running = False
            self.reconnecting = False

    def start(self):
        self.log_info(f"Connecting to {self.port}...")
        if not self._open_port(timeout_limit=10.0):
            self.log_error(f"Could not open serial port {self.port} within 10 seconds.")
            sys.exit(1)

        print(f"[+] Connected to {self.port} at {self.baudrate} baud.")
        print("[+] Type your commands below. Press Ctrl+C to exit.\n")

        # Start the background reader thread
        reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        reader_thread.start()

        # Start the background prompt manager thread
        prompt_thread = threading.Thread(target=self._prompt_loop, daemon=True)
        prompt_thread.start()

        # Main thread handles user input injection
        try:
            self._write_loop()
        except KeyboardInterrupt:
            print("\n\n[+] Exiting terminal...")
        finally:
            self.running = False
            if self.ser and self.ser.is_open:
                self.ser.close()

    def _read_loop(self):
        """Continuously reads data from the serial port in the background."""
        while self.running:
            if self.reconnecting:
                time.sleep(0.1)
                continue
                
            try:
                if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                    # Read all available bytes
                    data = self.ser.read(self.ser.in_waiting)
                    decoded_data = data.decode("utf-8", errors="ignore")
                    
                    if decoded_data:
                        # Clear the current prompt line if it's there
                        sys.stdout.write("\r\x1b[K") 
                        # Print incoming serial data
                        sys.stdout.write(decoded_data)
                        sys.stdout.flush()
                        
                        # Reset the idle timer and declare that a new prompt is needed
                        self.last_activity_time = time.time()
                        self.prompt_needs_redraw = True
            except (serial.SerialException, OSError):
                if self.running and not self.reconnecting:
                    self._handle_reconnect()
            except Exception as e:
                if self.running:
                    self.log_error(f"Unexpected read error: {e}")
                break
            time.sleep(0.01)  # Light sleep to avoid burning CPU

    def _prompt_loop(self):
        """Monitors idle time and handles the 200ms delayed prompt redraw."""
        while self.running:
            if self.reconnecting:
                time.sleep(0.1)
                continue

            # If data came in and 200 ms of silence has elapsed, redraw prompt
            if self.prompt_needs_redraw and (time.time() - self.last_activity_time >= 0.200):
                sys.stdout.write("\r\x1b[K> ")
                sys.stdout.flush()
                self.prompt_needs_redraw = False
            time.sleep(0.01)

    def _write_loop(self):
        """Reads input from local terminal and writes it immediately to the target."""
        while self.running:
            try:
                # Built-in input() blocks thread execution natively
                user_input = input()
                
                # If the script reconnected while the terminal input was blocking, update loop step
                if not self.running:
                    break

                # Queue packet transmissions only if port pipeline is active
                if self.reconnecting or not self.ser or not self.ser.is_open:
                    self.log_error("Cannot send command. Terminal is currently disconnected.")
                    continue

                # Append newline character matching your flasher protocol requirements
                payload = (user_input + "\n").encode("utf-8")
                
                try:
                    self.ser.write(payload)
                    self.ser.flush()
                    
                    # Reset prompt tracking variables for the delay cycle
                    self.last_activity_time = time.time()
                    self.prompt_needs_redraw = True
                except (serial.SerialException, OSError):
                    if self.running and not self.reconnecting:
                        self._handle_reconnect()

            except EOFError:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactive Serial Bootloader Terminal")
    parser.add_argument("port", help="Target serial port device path (e.g. /dev/ttyACM0)")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="Baudrate (default: 115200)")

    args = parser.parse_args()

    terminal = CustomTerminal(port=args.port, baudrate=args.baud)
    terminal.start()