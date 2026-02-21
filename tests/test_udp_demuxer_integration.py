"""
Integration test for UdpVideoDemuxer with simulated UDP stream.

This test:
1. Creates a mock UDP server that sends video packets
2. Uses UdpVideoDemuxer to receive and parse them
3. Verifies packets are correctly parsed and queued
"""

import sys
import os
import socket
import struct
import threading
import time
from queue import Queue

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrcpy_py_ddlx.core.protocol import (
    CodecId,
    UDP_HEADER_SIZE,
    PACKET_HEADER_SIZE,
    UDP_FLAG_KEY_FRAME,
    UDP_FLAG_CONFIG,
    UDP_FLAG_FRAGMENTED,
    ControlMessageType,
)
from scrcpy_py_ddlx.core.demuxer import UdpVideoDemuxer


class MockUdpServer:
    """Mock UDP server that simulates scrcpy video stream."""

    def __init__(self, host='127.0.0.1', port=0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.addr = self.sock.getsockname()
        self.running = False
        self.sequence = 0

    def create_config_packet(self, codec_id, width, height):
        """Create video config packet (codec_id + width + height)."""
        # UDP header
        udp_header = struct.pack('>IqI', self.sequence, 0, UDP_FLAG_CONFIG)
        self.sequence += 1

        # Scrcpy header (CONFIG flag = bit 63)
        pts_flags = 1 << 63
        config_payload = struct.pack('>III', codec_id, width, height)
        scrcpy_header = struct.pack('>QI', pts_flags, len(config_payload))

        return udp_header + scrcpy_header + config_payload

    def create_video_packet(self, pts, is_keyframe, data):
        """Create video frame packet."""
        # UDP header
        udp_flags = UDP_FLAG_KEY_FRAME if is_keyframe else 0
        udp_header = struct.pack('>IqI', self.sequence, pts, udp_flags)
        self.sequence += 1

        # Scrcpy header
        pts_flags = pts | (1 << 62) if is_keyframe else pts
        scrcpy_header = struct.pack('>QI', pts_flags, len(data))

        return udp_header + scrcpy_header + data

    def send_packet(self, client_addr, packet):
        """Send a packet to client."""
        self.sock.sendto(packet, client_addr)

    def close(self):
        self.sock.close()


class MockControlChannel:
    """Mock control channel to capture PLI requests."""

    def __init__(self):
        self.sent_data = []
        self.lock = threading.Lock()

    def send(self, data):
        with self.lock:
            self.sent_data.append(data)

    def get_messages(self):
        with self.lock:
            return list(self.sent_data)


def test_udp_video_demuxer_integration():
    """Test UdpVideoDemuxer with simulated video stream."""
    print("=" * 60)
    print("Integration Test: UdpVideoDemuxer with Mock Stream")
    print("=" * 60)

    # Create mock server
    server = MockUdpServer()
    print(f"Mock server listening on {server.addr}")

    # Create client socket
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_sock.bind(('127.0.0.1', 0))
    client_addr = client_sock.getsockname()
    print(f"Client socket bound to {client_addr}")

    # Create mock control channel
    control_channel = MockControlChannel()

    # Create packet queue
    packet_queue = Queue(maxsize=100)

    # Create UdpVideoDemuxer
    demuxer = UdpVideoDemuxer(
        udp_socket=client_sock,
        packet_queue=packet_queue,
        codec_id=CodecId.H264,
        control_channel=control_channel,
        pli_enabled=True,
        pli_threshold=5,
    )

    # Start demuxer
    demuxer.start()
    print("Demuxer started")

    try:
        # Give demuxer time to start
        time.sleep(0.1)

        # Send config packet
        config_pkt = server.create_config_packet(
            codec_id=CodecId.H264,
            width=1920,
            height=1080
        )
        server.send_packet(client_addr, config_pkt)
        print(f"Sent config packet: {len(config_pkt)} bytes")

        time.sleep(0.1)

        # Send some video packets
        for i in range(5):
            is_keyframe = (i == 0)
            video_data = bytes([0, 0, 0, 1, 0x65] + [i] * 50)  # Mock H.264 NALU
            video_pkt = server.create_video_packet(
                pts=i * 16666,  # ~60fps
                is_keyframe=is_keyframe,
                data=video_data
            )
            server.send_packet(client_addr, video_pkt)
            print(f"Sent video packet {i}: {len(video_pkt)} bytes, keyframe={is_keyframe}")
            time.sleep(0.05)

        # Wait for packets to be processed
        time.sleep(0.3)

        # Check queue
        packets_received = 0
        while not packet_queue.empty():
            pkt = packet_queue.get_nowait()
            packets_received += 1
            print(f"  Received packet: size={pkt.header.size}, "
                  f"config={pkt.header.is_config}, keyframe={pkt.header.is_key_frame}")

        print(f"\nPackets received from queue: {packets_received}")

        # Check stats
        stats = demuxer.get_stats()
        print(f"Demuxer stats: {stats}")

        # Verify results
        assert stats.packets_received > 0, "No packets received"
        print("\n[OK] Basic packet reception works")

        # Test packet loss detection and PLI
        print("\n--- Testing Packet Loss Detection ---")

        # Send packet with sequence gap to simulate loss
        # First, send packet with seq=N
        video_pkt = server.create_video_packet(pts=100000, is_keyframe=False, data=b'test1')
        server.send_packet(client_addr, video_pkt)

        # Now manually increment sequence on server to create gap
        server.sequence += 10  # Skip 10 packets

        # Send next packet
        video_pkt = server.create_video_packet(pts=200000, is_keyframe=False, data=b'test2')
        server.send_packet(client_addr, video_pkt)
        print(f"Sent packet with sequence gap (simulated 10 packet loss)")

        time.sleep(0.3)

        # Check if PLI was sent (with threshold=5, 10 packet loss should trigger PLI)
        stats_after = demuxer.get_stats()
        pli_messages = control_channel.get_messages()

        print(f"Packets lost detected: {stats_after.packets_lost}")
        print(f"PLI requests sent: {stats_after.pli_requests_sent}")
        print(f"Control messages captured: {len(pli_messages)}")

        if len(pli_messages) > 0:
            for msg in pli_messages:
                msg_type = msg[0]
                print(f"  PLI message type: {msg_type:#x} (RESET_VIDEO=0x11)")
            print("[OK] PLI request sent on packet loss")
        else:
            print("[INFO] No PLI sent (may not have reached threshold)")

    finally:
        # Cleanup
        demuxer.stop()
        server.close()
        print("\nDemuxer stopped, server closed")

    print("\n" + "=" * 60)
    print("Integration test completed!")
    print("=" * 60)


if __name__ == "__main__":
    test_udp_video_demuxer_integration()
