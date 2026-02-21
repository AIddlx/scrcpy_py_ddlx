#!/usr/bin/env python3
"""Debug UDP packet content"""

import socket
import struct

DEVICE_IP = "192.168.5.4"
CONTROL_PORT = 27184
VIDEO_PORT = 27185

def main():
    # Bind UDP
    video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    video_sock.settimeout(5)
    video_sock.bind(("0.0.0.0", VIDEO_PORT))
    print(f"UDP bound to 0.0.0.0:{VIDEO_PORT}")

    # Connect TCP
    control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_sock.settimeout(5)
    control_sock.connect((DEVICE_IP, CONTROL_PORT))
    print(f"TCP connected to {DEVICE_IP}:{CONTROL_PORT}")

    # Read dummy byte and device name
    dummy = control_sock.recv(1)
    device_name = control_sock.recv(64)
    print(f"Device: {device_name.rstrip(b'\\x00').decode()}")

    print("\n=== Receiving UDP packets (raw) ===")

    for i in range(5):
        try:
            data, addr = video_sock.recvfrom(65535)
            print(f"\nPacket #{i+1}: {len(data)} bytes from {addr}")

            # Parse UDP header (16 bytes)
            if len(data) >= 16:
                seq, ts, flags = struct.unpack('>IqI', data[:16])
                payload = data[16:]
                print(f"  UDP header: seq={seq}, ts={ts}, flags={flags:#x}")
                print(f"  Payload: {len(payload)} bytes")
                print(f"  Payload hex (first 32 bytes): {payload[:32].hex()}")

                # Try to parse as scrcpy packet
                if len(payload) >= 12:
                    pts_flags, size = struct.unpack('>QI', payload[:12])
                    is_config = bool(pts_flags & (1 << 63))
                    is_keyframe = bool(pts_flags & (1 << 62))
                    pts = pts_flags & 0x3FFFFFFFFFFFFFFF
                    print(f"  Scrcpy header: pts={pts}, size={size}, config={is_config}, keyframe={is_keyframe}")

                    if size <= len(payload) - 12:
                        scrcpy_payload = payload[12:12+size]
                        print(f"  Scrcpy payload ({size} bytes): {scrcpy_payload[:20].hex()}...")
                    else:
                        print(f"  WARNING: size={size} > available={len(payload)-12}")
        except socket.timeout:
            print(f"Timeout waiting for packet #{i+1}")

    control_sock.close()
    video_sock.close()

if __name__ == "__main__":
    main()
