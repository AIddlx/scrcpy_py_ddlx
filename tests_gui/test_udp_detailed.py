#!/usr/bin/env python3
"""Detailed UDP test with packet analysis"""

import socket
import struct
import time

DEVICE_IP = "192.168.5.4"
CONTROL_PORT = 27184
VIDEO_PORT = 27185

def main():
    # First, bind UDP socket BEFORE connecting TCP
    print("Binding UDP socket...")
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.settimeout(0.5)  # Short timeout for responsiveness
    udp_sock.bind(("0.0.0.0", VIDEO_PORT))
    print(f"UDP bound to 0.0.0.0:{VIDEO_PORT}")

    # Connect TCP
    print(f"\nConnecting TCP to {DEVICE_IP}:{CONTROL_PORT}...")
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.settimeout(5)
    tcp_sock.connect((DEVICE_IP, CONTROL_PORT))
    print("TCP connected!")

    # Read handshake
    dummy = tcp_sock.recv(1)
    device_name = tcp_sock.recv(64)
    print(f"Device: {device_name.rstrip(b'\\x00').decode()}")

    # Now receive UDP with detailed logging
    print("\n=== Receiving UDP (detailed) ===")
    start_time = time.time()
    packet_count = 0
    byte_count = 0
    empty_count = 0

    while time.time() - start_time < 10:  # 10 seconds
        try:
            data, addr = udp_sock.recvfrom(65535)
            recv_time = time.time() - start_time

            if len(data) == 0:
                empty_count += 1
                print(f"[{recv_time:.3f}s] EMPTY from {addr}")
            elif len(data) >= 16:  # Has our header
                packet_count += 1
                byte_count += len(data)
                seq = struct.unpack(">I", data[0:4])[0]  # Big endian
                ts = struct.unpack(">Q", data[4:12])[0]
                flags = struct.unpack(">I", data[12:16])[0]
                print(f"[{recv_time:.3f}s] {len(data)}B seq={seq} ts={ts} flags={flags:#x} from {addr}")
            else:
                packet_count += 1
                byte_count += len(data)
                print(f"[{recv_time:.3f}s] {len(data)}B (no header) from {addr}")

        except socket.timeout:
            continue
        except Exception as e:
            print(f"Error: {e}")
            break

    print(f"\n=== Summary ===")
    print(f"Valid packets: {packet_count}")
    print(f"Empty packets: {empty_count}")
    print(f"Total bytes: {byte_count}")

    tcp_sock.close()
    udp_sock.close()

if __name__ == "__main__":
    main()
