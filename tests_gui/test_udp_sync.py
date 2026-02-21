#!/usr/bin/env python3
"""Synchronized UDP test - start listening before server"""

import socket
import subprocess
import time
import threading

DEVICE_IP = "192.168.5.4"
CONTROL_PORT = 27184
VIDEO_PORT = 27185

def listen_udp():
    """Listen for UDP packets"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)
    sock.bind(("0.0.0.0", VIDEO_PORT))
    print(f"UDP listening on 0.0.0.0:{VIDEO_PORT}")

    packets = []
    empty_count = 0
    timeout_count = 0

    for i in range(100):  # 100 iterations, 1 second each
        try:
            data, addr = sock.recvfrom(65535)
            if len(data) == 0:
                empty_count += 1
            else:
                packets.append((len(data), addr, data[:20]))
                print(f"[{i}] Got {len(data)} bytes from {addr}")
        except socket.timeout:
            timeout_count += 1

    sock.close()
    return packets, empty_count, timeout_count

def main():
    # Start UDP listener in background
    print("Starting UDP listener...")
    udp_thread = threading.Thread(target=lambda: None)
    udp_thread.start()

    # Start listening
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)
    sock.bind(("0.0.0.0", VIDEO_PORT))
    print(f"UDP socket bound to 0.0.0.0:{VIDEO_PORT}")

    # Kill old server
    print("Killing old server...")
    subprocess.run(["adb", "shell", "pkill -9 -f app_process"], capture_output=True)
    time.sleep(1)

    # Start new server
    print("Starting new server...")
    server_cmd = (
        "CLASSPATH=/data/local/tmp/scrcpy-server.apk app_process / "
        "com.genymobile.scrcpy.Server 3.3.4 log_level=debug "
        "control_port=27184 video_port=27185 audio_port=27186 "
        "video=true audio=false control=true send_device_meta=true send_dummy_byte=true cleanup=true"
    )
    subprocess.Popen(
        ["adb", "shell", server_cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print("Server starting...")

    # Wait for server to start and listen
    time.sleep(3)

    # Connect TCP control
    print(f"Connecting TCP to {DEVICE_IP}:{CONTROL_PORT}...")
    control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_sock.settimeout(5)
    try:
        control_sock.connect((DEVICE_IP, CONTROL_PORT))
        print("TCP connected!")

        dummy = control_sock.recv(1)
        print(f"Dummy byte: {dummy.hex()}")

        device_name = control_sock.recv(64)
        print(f"Device: {device_name.rstrip(b'\\x00').decode()}")
    except Exception as e:
        print(f"TCP error: {e}")
        return

    # Now receive UDP
    print("\nReceiving UDP packets...")
    packets = []
    empty_count = 0

    for i in range(50):
        try:
            data, addr = sock.recvfrom(65535)
            if len(data) == 0:
                empty_count += 1
                print(f"[{i}] EMPTY packet from {addr}")
            else:
                packets.append((len(data), addr))
                seq = int.from_bytes(data[:4], 'big') if len(data) >= 4 else -1
                print(f"[{i}] {len(data)} bytes, seq={seq}, from {addr}")
        except socket.timeout:
            pass  # Silent timeout

    print(f"\n=== Summary ===")
    print(f"Valid packets: {len(packets)}")
    print(f"Empty packets: {empty_count}")
    if packets:
        print(f"First packet size: {packets[0][0]}")
        print(f"Last packet size: {packets[-1][0]}")

    control_sock.close()
    sock.close()

if __name__ == "__main__":
    main()
