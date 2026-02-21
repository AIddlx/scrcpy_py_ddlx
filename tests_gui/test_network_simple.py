#!/usr/bin/env python3
"""Simple network mode test - connect to running server"""

import socket
import time

DEVICE_IP = "192.168.5.4"
CONTROL_PORT = 27184
VIDEO_PORT = 27185

def test_network_connection():
    print(f"Connecting to {DEVICE_IP}:{CONTROL_PORT}...")

    # Create TCP control socket
    control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_sock.settimeout(5)

    try:
        control_sock.connect((DEVICE_IP, CONTROL_PORT))
        print("TCP control connected!")

        # Read dummy byte
        dummy = control_sock.recv(1)
        print(f"Received dummy byte: {dummy.hex()}")

        # Read device name (64 bytes)
        device_name = control_sock.recv(64)
        device_name_str = device_name.rstrip(b'\x00').decode('utf-8')
        print(f"Device name: {device_name_str}")

        # Create UDP video socket
        video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        video_sock.settimeout(5)
        video_sock.bind(("0.0.0.0", VIDEO_PORT))
        print(f"UDP video socket bound to 0.0.0.0:{VIDEO_PORT}")

        print("Waiting for video data (filtering 0-byte packets)...")
        valid_packets = 0
        empty_packets = 0
        for i in range(30):  # More iterations
            try:
                data, addr = video_sock.recvfrom(65535)
                if len(data) == 0:
                    empty_packets += 1
                    print(f"  [ignored] Empty packet from {addr}")
                    continue
                valid_packets += 1
                print(f"Received {len(data)} bytes from {addr} (seq: {int.from_bytes(data[:4], 'big') if len(data) >= 4 else 'N/A'})")
                if valid_packets >= 5:
                    print("Got enough packets, stopping.")
                    break
            except socket.timeout:
                print(f"Timeout {i+1}/30")

        print(f"\nSummary: {valid_packets} valid packets, {empty_packets} empty packets ignored")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        control_sock.close()
        print("Done")

if __name__ == "__main__":
    test_network_connection()
