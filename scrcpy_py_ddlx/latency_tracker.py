"""
Latency Tracker for analyzing frame processing delays.

Tracks multiple pipeline stages:
1. UDP receive → queue (packet processing)
2. Queue → decode start (queue wait time)
3. Decode start → decode complete (decode time)
4. Decode complete → shm write (post-decode)
5. Total: UDP receive → shm write

Also tracks preview side:
6. shm write → shm read (inter-process latency)
7. shm read → render (preview render time)

Automatically enabled, logs to file every 60 frames.
Each frame gets a unique tag [Fxxxxx] for tracking.
"""

import time
import threading
import logging
from collections import deque
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class LatencyTracker:
    """Multi-stage latency tracker for video pipeline with frame tagging."""

    def __init__(self, enabled: bool = True, history_size: int = 100, log_interval: int = 60):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._log_interval = log_interval

        # Stage timings (in milliseconds)
        self._udp_to_queue: deque = deque(maxlen=history_size)      # UDP receive → queue put
        self._queue_to_decode: deque = deque(maxlen=history_size)   # Queue get → decode start
        self._decode_time: deque = deque(maxlen=history_size)       # Decode duration
        self._decode_to_shm: deque = deque(maxlen=history_size)     # Decode complete → shm write
        self._total_pipeline: deque = deque(maxlen=history_size)    # UDP receive → shm write
        self._shm_write_to_read: deque = deque(maxlen=history_size) # shm write → shm read
        self._shm_read_to_render: deque = deque(maxlen=history_size) # shm read → render

        # Tracking state: packet_id → {stage: timestamp}
        self._packet_times: Dict[int, Dict[str, float]] = {}
        self._next_packet_id = 0

        self._frame_count = 0

        # Debug: track queue sizes
        self._queue_sizes: deque = deque(maxlen=history_size)

    def start_packet(self) -> int:
        """Start tracking a new packet. Returns packet ID."""
        if not self.enabled:
            return -1
        with self._lock:
            packet_id = self._next_packet_id
            self._next_packet_id += 1
            now = time.time()
            self._packet_times[packet_id] = {
                'udp_recv': now
            }
            # Log every frame with tag
            logger.debug(f"[F{packet_id:05d}] UDP_RECV at {now:.3f}")
            return packet_id

    def start_packet_with_time(self, udp_recv_time: float, pts: int = 0) -> int:
        """
        Start tracking a new packet with a specific UDP receive time.

        This is used when the actual UDP receive time is known (e.g., from socket recv).

        Args:
            udp_recv_time: The actual time when the UDP packet was received (time.time())
            pts: Presentation timestamp from device (nanoseconds) - for frame verification

        Returns:
            Packet ID for tracking
        """
        if not self.enabled:
            return -1
        with self._lock:
            packet_id = self._next_packet_id
            self._next_packet_id += 1
            self._packet_times[packet_id] = {
                'udp_recv': udp_recv_time,
                'pts': pts  # Store PTS for frame verification
            }
            # Log every frame with tag and PTS
            logger.debug(f"[F{packet_id:05d}] UDP_RECV at {udp_recv_time:.3f}, pts={pts} (explicit)")
            return packet_id

    def record_queue_put(self, packet_id: int, queue_size: int = -1):
        """Record when packet is put in queue."""
        if not self.enabled or packet_id < 0:
            return
        with self._lock:
            if packet_id in self._packet_times:
                now = time.time()
                self._packet_times[packet_id]['queue_put'] = now
                self._packet_times[packet_id]['queue_size_at_put'] = queue_size
                if queue_size >= 0:
                    self._queue_sizes.append(queue_size)
                # Log queue wait start
                elapsed = (now - self._packet_times[packet_id]['udp_recv']) * 1000
                logger.debug(f"[F{packet_id:05d}] QUEUE_PUT qsize={queue_size} elapsed={elapsed:.1f}ms")

    def record_decode_start(self, packet_id: int):
        """Record when decode starts."""
        if not self.enabled or packet_id < 0:
            return
        with self._lock:
            if packet_id in self._packet_times:
                now = time.time()
                self._packet_times[packet_id]['decode_start'] = now
                # Log queue wait time
                if 'queue_put' in self._packet_times[packet_id]:
                    queue_wait = (now - self._packet_times[packet_id]['queue_put']) * 1000
                    logger.debug(f"[F{packet_id:05d}] DECODE_START queue_wait={queue_wait:.1f}ms")

    def record_decode_complete(self, packet_id: int):
        """Record when decode completes."""
        if not self.enabled or packet_id < 0:
            return
        with self._lock:
            if packet_id in self._packet_times:
                now = time.time()
                self._packet_times[packet_id]['decode_complete'] = now
                # Log decode time
                if 'decode_start' in self._packet_times[packet_id]:
                    decode_time = (now - self._packet_times[packet_id]['decode_start']) * 1000
                    logger.debug(f"[F{packet_id:05d}] DECODE_COMPLETE decode_time={decode_time:.1f}ms")

    def record_shm_write(self, packet_id: int):
        """Record when frame is written to shared memory."""
        if not self.enabled or packet_id < 0:
            return
        with self._lock:
            if packet_id in self._packet_times:
                now = time.time()
                self._packet_times[packet_id]['shm_write'] = now
                # Log total pipeline time
                if 'udp_recv' in self._packet_times[packet_id]:
                    total = (now - self._packet_times[packet_id]['udp_recv']) * 1000
                    logger.info(f"[F{packet_id:05d}] SHM_WRITE total_pipeline={total:.1f}ms")
                self._calculate_latencies(packet_id)

    def record_shm_read(self, packet_id: int):
        """Record when frame is read from shared memory (preview process)."""
        if not self.enabled or packet_id < 0:
            return
        with self._lock:
            if packet_id in self._packet_times:
                now = time.time()
                self._packet_times[packet_id]['shm_read'] = now
                times = self._packet_times[packet_id]
                if 'shm_write' in times:
                    latency = (now - times['shm_write']) * 1000
                    self._shm_write_to_read.append(latency)
                    logger.info(f"[F{packet_id:05d}] SHM_READ ipc_latency={latency:.1f}ms")

    def record_render(self, packet_id: int):
        """Record when frame is rendered (preview process)."""
        if not self.enabled or packet_id < 0:
            return
        with self._lock:
            if packet_id in self._packet_times:
                now = time.time()
                self._packet_times[packet_id]['render'] = now
                times = self._packet_times[packet_id]
                if 'shm_read' in times:
                    latency = (now - times['shm_read']) * 1000
                    self._shm_read_to_render.append(latency)
                # Log end-to-end latency
                if 'udp_recv' in times:
                    e2e = (now - times['udp_recv']) * 1000
                    logger.info(f"[F{packet_id:05d}] RENDER e2e_latency={e2e:.1f}ms")

    def get_udp_recv_time(self, packet_id: int) -> float:
        """Get the UDP receive time for a packet."""
        if not self.enabled or packet_id < 0:
            return 0.0
        with self._lock:
            if packet_id in self._packet_times:
                return self._packet_times[packet_id].get('udp_recv', 0.0)
            return 0.0

    def get_pts(self, packet_id: int) -> int:
        """Get the PTS (presentation timestamp) for a packet."""
        if not self.enabled or packet_id < 0:
            return 0
        with self._lock:
            if packet_id in self._packet_times:
                return self._packet_times[packet_id].get('pts', 0)
            return 0

    def _calculate_latencies(self, packet_id: int):
        """Calculate and record latencies for a completed packet."""
        times = self._packet_times.get(packet_id)
        if not times:
            return

        # Calculate stage latencies
        if 'udp_recv' in times and 'queue_put' in times:
            latency = (times['queue_put'] - times['udp_recv']) * 1000
            self._udp_to_queue.append(latency)

        if 'queue_put' in times and 'decode_start' in times:
            latency = (times['decode_start'] - times['queue_put']) * 1000
            self._queue_to_decode.append(latency)

        if 'decode_start' in times and 'decode_complete' in times:
            latency = (times['decode_complete'] - times['decode_start']) * 1000
            self._decode_time.append(latency)

        if 'decode_complete' in times and 'shm_write' in times:
            latency = (times['shm_write'] - times['decode_complete']) * 1000
            self._decode_to_shm.append(latency)

        if 'udp_recv' in times and 'shm_write' in times:
            latency = (times['shm_write'] - times['udp_recv']) * 1000
            self._total_pipeline.append(latency)

        # Cleanup old entries (keep last 100)
        if len(self._packet_times) > 100:
            oldest = sorted(self._packet_times.keys())[:-100]
            for pid in oldest:
                del self._packet_times[pid]

        self._frame_count += 1
        if self._frame_count % self._log_interval == 0:
            self._log_stats()

    def _log_stats(self):
        """Log stats to file."""
        lines = ["[LATENCY] Pipeline stage analysis:"]

        if self._udp_to_queue:
            avg = sum(self._udp_to_queue) / len(self._udp_to_queue)
            lines.append(f"  UDP→Queue:  avg={avg:.2f}ms")

        if self._queue_to_decode:
            avg = sum(self._queue_to_decode) / len(self._queue_to_decode)
            max_v = max(self._queue_to_decode)
            lines.append(f"  Queue→Dec:  avg={avg:.2f}ms, max={max_v:.2f}ms")

        if self._decode_time:
            avg = sum(self._decode_time) / len(self._decode_time)
            max_v = max(self._decode_time)
            lines.append(f"  Decode:     avg={avg:.2f}ms, max={max_v:.2f}ms")

        if self._decode_to_shm:
            avg = sum(self._decode_to_shm) / len(self._decode_to_shm)
            lines.append(f"  Dec→Shm:    avg={avg:.2f}ms")

        if self._total_pipeline:
            avg = sum(self._total_pipeline) / len(self._total_pipeline)
            max_v = max(self._total_pipeline)
            min_v = min(self._total_pipeline)
            lines.append(f"  TOTAL:      avg={avg:.2f}ms, min={min_v:.2f}ms, max={max_v:.2f}ms")

        if self._shm_write_to_read:
            avg = sum(self._shm_write_to_read) / len(self._shm_write_to_read)
            lines.append(f"  Shm→Read:   avg={avg:.2f}ms")

        if self._shm_read_to_render:
            avg = sum(self._shm_read_to_render) / len(self._shm_read_to_render)
            lines.append(f"  Read→Render: avg={avg:.2f}ms")

        if self._queue_sizes:
            avg_q = sum(self._queue_sizes) / len(self._queue_sizes)
            max_q = max(self._queue_sizes)
            lines.append(f"  QueueSize:  avg={avg_q:.1f}, max={max_q}")

        lines.append(f"  Frames: {self._frame_count}")

        logger.info("\n".join(lines))


# Global instance - auto enabled
_tracker: Optional[LatencyTracker] = None


def get_tracker() -> LatencyTracker:
    global _tracker
    if _tracker is None:
        _tracker = LatencyTracker(enabled=True)
    return _tracker
