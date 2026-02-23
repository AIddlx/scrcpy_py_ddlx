"""
FEC (Forward Error Correction) Decoder for UDP video streams.

This module implements FEC decoding using XOR parity for packet recovery.
When packets are lost, the decoder can recover them using parity packets
as long as the number of lost packets does not exceed the number of
available parity packets.

FEC Group Structure:
    Data Packets:   [D0] [D1] [D2] [D3] ... [DK-1]  (0-based indices)
    Parity Packets: [P0] [P1] ... [PM-1]

    Recovery: If N packets are lost and M >= N parity packets are available,
              the lost packets can be recovered using XOR.

FEC Header Formats:
    Data Header (7 bytes):
      - group_id: 2B (uint16, big-endian)
      - packet_idx: 1B (uint8) - index in group (0 to K-1)
      - total_data: 1B (uint8) - K
      - total_parity: 1B (uint8) - M
      - original_size: 2B (uint16, big-endian) - original payload size for recovery

    Parity Header (5 bytes):
      - group_id: 2B (uint16, big-endian)
      - parity_idx: 1B (uint8) - index (0 to M-1)
      - total_data: 1B (uint8) - K
      - total_parity: 1B (uint8) - M
"""

import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class FecGroupBuffer:
    """Buffer for a single FEC group being received.

    Uses 0-based indexing: packet indices are 0 to K-1 for K data packets.
    """
    group_id: int
    total_data_packets: int  # K
    total_parity_packets: int  # M
    created_at: float = field(default_factory=time.time)

    # Received packets
    data_packets: Dict[int, bytes] = field(default_factory=dict)  # idx -> data
    parity_packets: Dict[int, bytes] = field(default_factory=dict)  # idx -> data

    # Original sizes for each packet (for recovery truncation)
    original_sizes: Dict[int, int] = field(default_factory=dict)  # idx -> original size

    # Tracking
    first_scrcpy_header: Optional[bytes] = None  # Scrcpy header from first data packet

    @property
    def is_complete(self) -> bool:
        """Check if all data packets are received (0-based: 0 to K-1)."""
        if self.total_data_packets == 0:
            return False
        K = self.total_data_packets
        # Server sends idx=0,1,2,...,K-1 (K packets total)
        # Check if we have all packets from 0 to K-1
        return all(i in self.data_packets for i in range(K))

    @property
    def missing_count(self) -> int:
        """Number of missing data packets."""
        # If total_data_packets is 0, we can't determine missing count
        if self.total_data_packets == 0:
            return 0
        return self.total_data_packets - len(self.data_packets)

    @property
    def can_recover(self) -> bool:
        """Check if we have enough parity to recover missing packets."""
        # Can't recover if we don't know the expected packet count
        if self.total_data_packets == 0:
            return False
        return len(self.parity_packets) >= self.missing_count and self.missing_count > 0

    def get_missing_indices(self) -> Set[int]:
        """Get indices of missing data packets (0-based: 0 to K-1)."""
        return set(range(self.total_data_packets)) - set(self.data_packets.keys())


# =============================================================================
# FecDecoder
# =============================================================================

class FecDecoder:
    """
    FEC Decoder using XOR parity.

    Usage:
        decoder = FecDecoder()

        # Add received data packet
        decoder.add_data_packet(group_id=0, packet_idx=2, total=4,
                                parity_count=1, data=packet_data)

        # Add received parity packet
        decoder.add_parity_packet(group_id=0, parity_idx=0, total=4,
                                  parity_data=parity_data)

        # Try to get complete group or recover
        result = decoder.try_recover(group_id=0)
        if result:
            # result is List[bytes] - the complete data packets in order
            for pkt in result:
                process(pkt)

        # Check for failed groups and trigger PLI if needed
        failed_count = decoder.get_and_reset_failed_count()
        if failed_count > 0:
            send_pli()
    """

    # Maximum groups to track
    MAX_GROUPS = 50

    # Group timeout in seconds (reduced for frame-level FEC)
    GROUP_TIMEOUT = 0.1  # 100ms - enough for parity packet to arrive

    def __init__(self):
        self._groups: Dict[int, FecGroupBuffer] = {}
        self._stats = {
            'groups_completed': 0,
            'groups_recovered': 0,
            'packets_recovered': 0,
            'groups_failed': 0,
        }
        self._recent_failures = 0  # Track recent failures for PLI trigger

    # -------------------------------------------------------------------------
    # Public Methods
    # -------------------------------------------------------------------------

    def add_data_packet(
        self,
        group_id: int,
        packet_idx: int,
        total_data: int,
        total_parity: int,
        data: bytes,
        original_size: Optional[int] = None
    ) -> Optional[List[bytes]]:
        """
        Add a received data packet to the FEC group.

        Args:
            group_id: FEC group identifier
            packet_idx: Index of this packet in the group (0 to K-1)
            total_data: Total data packets in group (K)
            total_parity: Total parity packets in group (M)
            data: Packet data (including scrcpy header)
            original_size: Original size of the packet (for recovery truncation)

        Returns:
            Complete group data if group is now complete, None otherwise
        """
        # Get or create group
        group = self._get_or_create_group(group_id, total_data, total_parity)

        # Store packet
        group.data_packets[packet_idx] = data

        # Debug: log packet storage
        logger.debug(
            f"FEC add_data_packet: group={group_id}, idx={packet_idx}, "
            f"stored={len(group.data_packets)}/{total_data}"
        )

        # Store original size for recovery
        if original_size is not None:
            group.original_sizes[packet_idx] = original_size
        else:
            # If not provided, use actual data size
            group.original_sizes[packet_idx] = len(data)

        # Store scrcpy header from first packet for later use
        if packet_idx == 0 and len(data) >= 12:
            group.first_scrcpy_header = data[:12]

        # Check if complete
        if group.is_complete:
            logger.info(f"FEC group {group_id} complete with {len(group.data_packets)} packets")
            self._cleanup_group(group_id)
            self._stats['groups_completed'] += 1
            return self._get_ordered_data(group)

        # Try recovery if possible
        if group.can_recover:
            result = self._try_recover_group(group)
            if result:
                self._cleanup_group(group_id)
                return result

        return None

    def add_parity_packet(
        self,
        group_id: int,
        parity_idx: int,
        total_data: int,
        total_parity: int,
        parity_data: bytes
    ) -> Optional[List[bytes]]:
        """
        Add a received parity packet to the FEC group.

        Args:
            group_id: FEC group identifier
            parity_idx: Index of this parity packet (0 to M-1)
            total_data: Total data packets in group (K)
            total_parity: Total parity packets in group (M)
            parity_data: Parity packet data

        Returns:
            Complete group data if recovery succeeds, None otherwise
        """
        # Only add parity to existing group - don't create new group
        # If group doesn't exist, it means data packets were already received and processed
        if group_id not in self._groups:
            logger.debug(
                f"FEC parity ignored for group {group_id}: group already completed or not started"
            )
            return None

        group = self._groups[group_id]

        # IMPORTANT: Update total_data from parity packet
        # In frame-based FEC, data packets have total_data=0 (unknown at send time)
        # Parity packets have the correct total_data (actual packet count in frame)
        if total_data > 0 and group.total_data_packets == 0:
            group.total_data_packets = total_data
            logger.debug(
                f"FEC group {group_id} total_data updated from parity: {total_data}"
            )

        # Store parity
        group.parity_packets[parity_idx] = parity_data

        # Check if already complete (frame-level FEC: total_data=1, all packets received)
        if group.is_complete:
            logger.debug(f"FEC group {group_id} complete (frame-level): {len(group.data_packets)}/{group.total_data_packets} packets")
            self._cleanup_group(group_id)
            self._stats['groups_completed'] += 1
            return self._get_ordered_data(group)

        # Try recovery (missing packets but enough parity)
        if group.can_recover:
            result = self._try_recover_group(group)
            if result:
                logger.info(f"FEC group {group_id} recovered: {group.missing_count} missing packets")
                self._cleanup_group(group_id)
                return result

        return None

    def try_recover(self, group_id: int) -> Optional[List[bytes]]:
        """
        Try to recover missing packets in a group.

        Returns complete group data if recovery succeeds, None otherwise.
        """
        if group_id not in self._groups:
            return None

        group = self._groups[group_id]

        if group.is_complete:
            self._cleanup_group(group_id)
            return self._get_ordered_data(group)

        if group.can_recover:
            result = self._try_recover_group(group)
            if result:
                self._cleanup_group(group_id)
                return result

        return None

    def get_stats(self) -> dict:
        """Get decoder statistics."""
        return dict(self._stats)

    def clear(self) -> dict:
        """
        Clear all groups and return stats before clearing.

        Called when video configuration changes (e.g., screen rotation),
        all old FEC groups are invalid for new resolution.
        """
        stats = dict(self._stats)
        count = len(self._groups)
        self._groups.clear()
        self._recent_failures = 0
        if count > 0:
            logger.info(f"FEC decoder cleared: {count} groups discarded")
        return stats

    def get_and_reset_failed_count(self) -> int:
        """
        Get and reset the count of recently failed groups.

        This is used to trigger PLI when FEC recovery fails.
        Returns the count of groups that failed since last call.
        """
        count = self._recent_failures
        self._recent_failures = 0
        return count

    # -------------------------------------------------------------------------
    # Private Methods
    # -------------------------------------------------------------------------

    def _get_or_create_group(
        self,
        group_id: int,
        total_data: int,
        total_parity: int
    ) -> FecGroupBuffer:
        """Get existing group or create new one."""
        if group_id not in self._groups:
            self._cleanup_old_groups()
            self._groups[group_id] = FecGroupBuffer(
                group_id=group_id,
                total_data_packets=total_data,
                total_parity_packets=total_parity,
            )
        return self._groups[group_id]

    def _try_recover_group(self, group: FecGroupBuffer) -> Optional[List[bytes]]:
        """
        Try to recover missing packets using XOR parity.

        XOR Recovery Algorithm:
            If D0 is missing and we have D1, D2, D3, P0 (where P0 = D0 ^ D1 ^ D2 ^ D3)
            Then: D0 = P0 ^ D1 ^ D2 ^ D3
        """
        missing = group.get_missing_indices()

        if len(missing) == 0:
            return self._get_ordered_data(group)

        if len(missing) > len(group.parity_packets):
            logger.debug(
                f"FEC group {group.group_id}: cannot recover, "
                f"missing={len(missing)}, parity={len(group.parity_packets)}"
            )
            return None

        # Get packet size - use PARITY size as it's calculated from max frame size
        # This ensures recovered packets have enough space for the largest frame
        packet_size = None
        if group.parity_packets:
            # Use parity packet size (it's the XOR of all frames, so it's the max size)
            first_parity_idx = min(group.parity_packets.keys())
            packet_size = len(group.parity_packets[first_parity_idx])

        if packet_size is None:
            # Fallback: no parity, use first data packet
            for pkt in group.data_packets.values():
                packet_size = len(pkt)
                break

        if packet_size is None:
            return None

        logger.info(
            f"FEC group {group.group_id}: attempting recovery of "
            f"{len(missing)} packets using {len(group.parity_packets)} parity"
        )

        # Recovery using XOR
        # For each missing packet, we need to XOR all other data packets and parity
        recovered_count = 0
        for missing_idx in list(missing):
            recovered = self._xor_recover(group, missing_idx, packet_size)
            if recovered is not None:
                group.data_packets[missing_idx] = recovered
                recovered_count += 1
                logger.debug(f"FEC: recovered packet {missing_idx} in group {group.group_id}")

        if group.is_complete:
            self._stats['groups_recovered'] += 1
            self._stats['packets_recovered'] += recovered_count
            logger.info(f"FEC group {group.group_id}: recovery successful!")
            return self._get_ordered_data(group)

        # Partial recovery - not enough parity
        logger.warning(
            f"FEC group {group.group_id}: partial recovery, "
            f"still missing {group.missing_count} packets"
        )
        return None

    def _xor_recover(
        self,
        group: FecGroupBuffer,
        missing_idx: int,
        expected_size: int
    ) -> Optional[bytes]:
        """
        Recover a single missing packet using XOR.

        XOR all received data packets and ONE parity packet.
        The result is the missing packet.

        Note: For simple XOR FEC where all parity packets are identical
        (P0 = P1 = ... = D0 XOR D1 XOR ... XOR Dk-1), we only need ONE
        parity packet for recovery. XOR-ing multiple identical parity
        packets would give us 0 (P XOR P = 0).
        """
        # Start with zeros
        result = bytearray(expected_size)

        # XOR all received data packets
        for idx, pkt in group.data_packets.items():
            self._xor_into(result, pkt)

        # XOR only ONE parity packet (they are all identical in simple XOR FEC)
        # Using the first available parity packet
        if group.parity_packets:
            first_parity_idx = min(group.parity_packets.keys())
            self._xor_into(result, group.parity_packets[first_parity_idx])

        recovered = bytes(result)

        # Determine correct size for truncation
        # Priority: 1) stored original_size, 2) scrcpy header payload_size, 3) no truncation
        truncate_size = len(recovered)

        if missing_idx in group.original_sizes:
            # We have stored original size
            truncate_size = group.original_sizes[missing_idx]
            logger.debug(f"FEC: using stored original_size={truncate_size} for packet {missing_idx}")
        elif len(recovered) >= 12:
            # Try to parse scrcpy header to get actual payload_size
            # Scrcpy header format: [pts_flags: 8B] [payload_size: 4B]
            try:
                import struct
                pts_flags, payload_size = struct.unpack('>QI', recovered[:12])
                # Sanity check: payload_size should be reasonable (< 16MB)
                if 0 < payload_size < 16777216:
                    # Total size = 12 (header) + payload_size
                    truncate_size = 12 + payload_size
                    logger.debug(
                        f"FEC: parsed scrcpy header for packet {missing_idx}, "
                        f"payload_size={payload_size}, total={truncate_size}"
                    )
            except Exception as e:
                logger.debug(f"FEC: failed to parse scrcpy header: {e}")

        if truncate_size < len(recovered):
            logger.debug(
                f"FEC: truncating recovered packet {missing_idx} "
                f"from {len(recovered)} to {truncate_size} bytes"
            )
            recovered = recovered[:truncate_size]

        return recovered

    @staticmethod
    def _xor_into(target: bytearray, source: bytes) -> None:
        """XOR source into target (in-place)."""
        for i in range(min(len(target), len(source))):
            target[i] ^= source[i]

    def _get_ordered_data(self, group: FecGroupBuffer) -> List[bytes]:
        """Get data packets in order (0-based: 0 to K-1)."""
        return [group.data_packets[i] for i in range(group.total_data_packets)]

    def _cleanup_group(self, group_id: int) -> None:
        """Remove a group from tracking."""
        if group_id in self._groups:
            del self._groups[group_id]

    def _cleanup_old_groups(self) -> None:
        """Remove expired groups."""
        now = time.time()
        expired = [
            gid for gid, g in self._groups.items()
            if now - g.created_at > self.GROUP_TIMEOUT
        ]

        for gid in expired:
            group = self._groups[gid]
            if not group.is_complete:
                self._stats['groups_failed'] += 1
                self._recent_failures += 1  # Track for PLI trigger
                logger.warning(
                    f"FEC group {gid} expired: "
                    f"received {len(group.data_packets)}/{group.total_data_packets} data, "
                    f"{len(group.parity_packets)}/{group.total_parity_packets} parity"
                )
            del self._groups[gid]

        # Also limit total count
        if len(self._groups) > self.MAX_GROUPS:
            # Remove oldest
            sorted_groups = sorted(self._groups.items(), key=lambda x: x[1].created_at)
            for gid, _ in sorted_groups[:len(self._groups) - self.MAX_GROUPS]:
                del self._groups[gid]


# =============================================================================
# FecEncoder (for reference, server-side implementation)
# =============================================================================

class SimpleXorFecEncoder:
    """
    Simple XOR FEC Encoder.

    This creates parity packets by XOR-ing all data packets together.
    For stronger protection, multiple parity packets can be created
    using different XOR combinations.
    """

    def __init__(self, group_size: int = 4, parity_count: int = 1):
        """
        Initialize encoder.

        Args:
            group_size: Number of data packets per group (K)
            parity_count: Number of parity packets per group (M)
        """
        self.group_size = group_size
        self.parity_count = parity_count

    def encode(self, data_packets: List[bytes]) -> List[bytes]:
        """
        Generate parity packets from data packets.

        Args:
            data_packets: List of K data packets

        Returns:
            List of M parity packets
        """
        if len(data_packets) != self.group_size:
            raise ValueError(f"Expected {self.group_size} packets, got {len(data_packets)}")

        # Get max size
        max_size = max(len(p) for p in data_packets)

        parity_packets = []

        for p in range(self.parity_count):
            # Create parity by XOR-ing all packets
            parity = bytearray(max_size)
            for pkt in data_packets:
                for i in range(len(pkt)):
                    parity[i] ^= pkt[i]

            parity_packets.append(bytes(parity))

        return parity_packets
