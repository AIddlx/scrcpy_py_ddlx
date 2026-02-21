package com.genymobile.scrcpy.udp;

import com.genymobile.scrcpy.util.Ln;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.ByteBuffer;
import java.util.List;

public class UdpMediaSender {
    private static final int MAX_PACKET_SIZE = 65507; // UDP max payload
    private static final int HEADER_SIZE = 24; // seq(4) + timestamp(8) + flags(4) + send_time_ns(8)

    public static final long FLAG_KEY_FRAME = 1L << 0;
    public static final long FLAG_CONFIG = 1L << 1;
    public static final long FLAG_FEC_DATA = 1L << 2;     // FEC data packet
    public static final long FLAG_FEC_PARITY = 1L << 3;   // FEC parity packet

    private final DatagramSocket socket;
    private final InetAddress clientAddress;
    private final int clientPort;
    private int sequence = 0;

    // FEC support (optional)
    private boolean fecEnabled = false;
    private SimpleXorFecEncoder fecEncoder;

    // Frame sequence number for FEC grouping
    private int frameSeq = 0;
    private long lastTimestamp = -1;

    public UdpMediaSender(DatagramSocket socket, InetAddress clientAddress, int clientPort) {
        this.socket = socket;
        this.clientAddress = clientAddress;
        this.clientPort = clientPort;
    }

    /**
     * Enable FEC with specified parameters.
     *
     * @param groupSize Number of frames per FEC group (K)
     * @param parityCount Number of parity packets per group (M)
     * @param fecMode FEC mode: "frame" or "fragment"
     */
    public void enableFec(int groupSize, int parityCount, String fecMode) {
        this.fecEnabled = true;
        this.fecEncoder = new SimpleXorFecEncoder(groupSize, parityCount);
        Ln.i("FEC enabled: K=" + groupSize + ", M=" + parityCount + ", mode=" + fecMode);
    }

    /**
     * Disable FEC.
     */
    public void disableFec() {
        this.fecEnabled = false;
        this.fecEncoder = null;
    }

    public boolean isFecEnabled() {
        return fecEnabled;
    }

    public void sendPacket(ByteBuffer data, long timestamp, boolean config, boolean keyFrame) throws IOException {
        int dataSize = data.remaining();
        long flags = 0;
        if (keyFrame) flags |= FLAG_KEY_FRAME;
        if (config) flags |= FLAG_CONFIG;

        // Debug log
        Ln.d("UDP sendPacket: size=" + dataSize + ", ts=" + timestamp + ", config=" + config + ", keyFrame=" + keyFrame);

        if (dataSize == 0) {
            Ln.w("Skipping empty UDP packet");
            return;
        }

        // If data fits in single packet
        if (dataSize + HEADER_SIZE <= MAX_PACKET_SIZE) {
            sendSinglePacket(data, timestamp, flags);
        } else {
            // Fragment large frames
            sendFragmented(data, timestamp, flags);
        }
    }

    private void sendSinglePacket(ByteBuffer data, long timestamp, long flags) throws IOException {
        int dataSize = data.remaining();
        long sendTimeNs = System.nanoTime();  // Device time when packet is sent

        ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + dataSize);
        packet.putInt(sequence++);
        packet.putLong(timestamp);
        packet.putInt((int) flags);
        packet.putLong(sendTimeNs);  // Add send time for E2E latency tracking
        packet.put(data);

        // Flip the buffer to prepare for reading
        packet.flip();

        int packetSize = packet.remaining();
        byte[] packetData = packet.array();

        // Debug: Log packet details for first few packets
        if (sequence <= 5) {
            StringBuilder hex = new StringBuilder();
            for (int i = 0; i < Math.min(32, packetSize); i++) {
                hex.append(String.format("%02x", packetData[i]));
            }
            Ln.d("UDP packet #" + (sequence-1) + ": size=" + packetSize + ", ts=" + timestamp +
                 ", flags=" + String.format("0x%x", flags) + ", send_ns=" + sendTimeNs + ", hex=" + hex.toString());
        }

        DatagramPacket dp = new DatagramPacket(packetData, packetSize, clientAddress, clientPort);
        socket.send(dp);
        Ln.d("UDP sent: " + packetSize + " bytes to " + clientAddress.getHostAddress() + ":" + clientPort);
    }

    private void sendFragmented(ByteBuffer data, long timestamp, long flags) throws IOException {
        // Fragmentation with sequence numbers
        // The first 12 bytes of data is the scrcpy header (pts_flags + size)
        // which must be included in the first fragment only

        long sendTimeNs = System.nanoTime();  // Device time when packet is sent (same for all fragments)
        int fragmentIndex = 0;
        int maxFragmentData = MAX_PACKET_SIZE - HEADER_SIZE - 4; // extra 4 for fragment index
        int totalData = data.remaining();

        // First fragment: includes scrcpy header (12 bytes) + as much data as fits
        // The scrcpy header is already at the beginning of 'data'
        int firstChunkSize = Math.min(totalData, maxFragmentData);
        ByteBuffer firstChunk = ByteBuffer.allocate(HEADER_SIZE + 4 + firstChunkSize);

        firstChunk.putInt(sequence++);
        firstChunk.putLong(timestamp);
        // Add fragmentation flag
        long fragFlags = flags | (1L << 31);
        firstChunk.putInt((int) fragFlags);
        firstChunk.putLong(sendTimeNs);  // Add send time for E2E latency tracking
        firstChunk.putInt(fragmentIndex++);

        // Copy first chunk (includes scrcpy header)
        byte[] temp = new byte[firstChunkSize];
        data.get(temp);
        firstChunk.put(temp);

        firstChunk.flip();
        DatagramPacket firstDp = new DatagramPacket(firstChunk.array(), firstChunk.remaining(), clientAddress, clientPort);
        socket.send(firstDp);

        // Subsequent fragments: just data (no scrcpy header)
        while (data.hasRemaining()) {
            int chunkSize = Math.min(data.remaining(), maxFragmentData);
            ByteBuffer chunk = ByteBuffer.allocate(HEADER_SIZE + 4 + chunkSize);

            chunk.putInt(sequence++);
            chunk.putLong(timestamp);
            chunk.putInt((int) fragFlags);
            chunk.putLong(sendTimeNs);  // Same send time for all fragments
            chunk.putInt(fragmentIndex++);

            byte[] chunkTemp = new byte[chunkSize];
            data.get(chunkTemp);
            chunk.put(chunkTemp);

            chunk.flip();
            DatagramPacket dp = new DatagramPacket(chunk.array(), chunk.remaining(), clientAddress, clientPort);
            socket.send(dp);
        }
    }

    public void close() {
        if (socket != null && !socket.isClosed()) {
            socket.close();
        }
    }

    // -------------------------------------------------------------------------
    // FEC Support
    // -------------------------------------------------------------------------

    /**
     * Send a packet with FEC header (for FEC data packets).
     * This wraps the data with FEC header and sends it with FEC_DATA flag.
     *
     * @param fecData FEC-wrapped data (already includes FEC header + payload)
     * @param timestamp Packet timestamp
     * @param keyFrame Whether this is a key frame
     */
    public void sendFecDataPacket(ByteBuffer fecData, long timestamp, boolean keyFrame) throws IOException {
        long flags = FLAG_FEC_DATA;
        if (keyFrame) flags |= FLAG_KEY_FRAME;

        long sendTimeNs = System.nanoTime();  // Device time when packet is sent
        int dataSize = fecData.remaining();

        ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + dataSize);
        packet.putInt(sequence++);
        packet.putLong(timestamp);
        packet.putInt((int) flags);
        packet.putLong(sendTimeNs);  // Add send time for E2E latency tracking
        packet.put(fecData);
        packet.flip();

        int packetSize = packet.remaining();
        byte[] packetData = packet.array();
        DatagramPacket dp = new DatagramPacket(packetData, packetSize, clientAddress, clientPort);
        socket.send(dp);
        Ln.d("FEC data sent: " + packetSize + " bytes, keyFrame=" + keyFrame);
    }

    /**
     * Send a FEC parity packet.
     * Handles fragmentation if parity data exceeds UDP MTU.
     *
     * @param fecParity FEC-wrapped parity (includes FEC header + parity data)
     * @param timestamp Packet timestamp
     */
    public void sendFecParityPacket(ByteBuffer fecParity, long timestamp) throws IOException {
        int dataSize = fecParity.remaining();
        int maxParityPayload = MAX_PACKET_SIZE - HEADER_SIZE - SimpleXorFecEncoder.getFecParityHeaderSize();

        if (dataSize <= maxParityPayload) {
            // Fits in single packet
            sendSingleFecParityPacket(fecParity, timestamp);
        } else {
            // Need to fragment
            sendFragmentedFecParityPacket(fecParity, timestamp);
        }
    }

    /**
     * Send a single (non-fragmented) FEC parity packet.
     */
    private void sendSingleFecParityPacket(ByteBuffer fecParity, long timestamp) throws IOException {
        long flags = FLAG_FEC_PARITY;
        long sendTimeNs = System.nanoTime();  // Device time when packet is sent
        int dataSize = fecParity.remaining();

        ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + dataSize);
        packet.putInt(sequence++);
        packet.putLong(timestamp);
        packet.putInt((int) flags);
        packet.putLong(sendTimeNs);  // Add send time for E2E latency tracking
        packet.put(fecParity);
        packet.flip();

        byte[] packetData = packet.array();
        DatagramPacket dp = new DatagramPacket(packetData, packet.remaining(), clientAddress, clientPort);
        socket.send(dp);

        Ln.d("FEC parity sent: " + dataSize + " bytes");
    }

    /**
     * Send a fragmented FEC parity packet.
     * Parity packets can be large (100KB+) and must be fragmented to fit UDP MTU.
     */
    private void sendFragmentedFecParityPacket(ByteBuffer fecParity, long timestamp) throws IOException {
        long flags = FLAG_FEC_PARITY | (1L << 31); // FEC_PARITY + FRAGMENTED
        long sendTimeNs = System.nanoTime();  // Device time when packet is sent (same for all fragments)
        int maxFragmentData = MAX_PACKET_SIZE - HEADER_SIZE - SimpleXorFecEncoder.getFecParityHeaderSize() - 4; // extra 4 for fragment index
        int totalSize = fecParity.remaining();
        int fragmentIndex = 0;
        int fragmentCount = (totalSize + maxFragmentData - 1) / maxFragmentData;

        Ln.d("FEC parity fragmenting: total=" + totalSize + " bytes into " + fragmentCount + " fragments");

        while (fecParity.hasRemaining()) {
            int chunkSize = Math.min(fecParity.remaining(), maxFragmentData);

            ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + chunkSize + 4); // +4 for fragment index
            packet.putInt(sequence++);
            packet.putLong(timestamp);
            packet.putInt((int) flags);
            packet.putLong(sendTimeNs);  // Add send time for E2E latency tracking
            packet.putInt(fragmentIndex);

            // Copy chunk of parity data
            byte[] temp = new byte[chunkSize];
            fecParity.get(temp);
            packet.put(temp);

            packet.flip();

            int packetSize = packet.remaining();
            byte[] packetData = packet.array();
            DatagramPacket dp = new DatagramPacket(packetData, packetSize, clientAddress, clientPort);
            socket.send(dp);

            fragmentIndex++;
        }

        Ln.d("FEC parity fragments sent: " + fragmentIndex + " fragments");
    }

    /**
     * Send a data packet with FEC protection.
     * Uses K-frame FEC grouping: K frames share one FEC group.
     * When K frames are accumulated, parity packets are generated and sent.
     *
     * @param data The scrcpy packet data
     * @param timestamp Packet timestamp (frame identifier)
     * @param config Whether this is a config packet
     * @param keyFrame Whether this is a key frame
     */
    public void sendPacketWithFec(ByteBuffer data, long timestamp, boolean config, boolean keyFrame) throws IOException {
        if (!fecEnabled || fecEncoder == null) {
            sendPacket(data, timestamp, config, keyFrame);
            return;
        }

        if (config) {
            sendPacket(data, timestamp, config, keyFrame);
            return;
        }

        // Check if this is a new frame (timestamp changed)
        boolean isNewFrame = (lastTimestamp != timestamp);
        long previousTimestamp = lastTimestamp;

        if (isNewFrame) {
            // Mark previous frame as complete BEFORE sending new frame
            if (fecEncoder.hasIncompleteGroup()) {
                fecEncoder.frameComplete();
            }
            lastTimestamp = timestamp;
        }

        // Send current frame data
        int dataSize = data.remaining();
        long flags = 0;
        if (keyFrame) flags |= FLAG_KEY_FRAME;

        int maxFecPayload = MAX_PACKET_SIZE - HEADER_SIZE - SimpleXorFecEncoder.getFecDataHeaderSize();

        if (dataSize <= maxFecPayload) {
            ByteBuffer fecWrapped = fecEncoder.addPacket(data);
            sendFecDataPacket(fecWrapped, timestamp, keyFrame);
        } else {
            sendFragmentedWithFec(data, timestamp, flags);
        }

        // Check if we just completed a FEC group (after K frames)
        // This check is done AFTER sending current frame data
        if (fecEncoder.shouldFinalizeGroup()) {
            int frameCount = fecEncoder.getCurrentGroupSize();
            List<ByteBuffer> parityPackets = fecEncoder.generateParityPackets();
            for (ByteBuffer parity : parityPackets) {
                sendFecParityPacket(parity, previousTimestamp);
            }
            Ln.i("FEC group complete: sent " + parityPackets.size() + " parity for " + frameCount + " frames");
        }
    }

    /**
     * Send fragmented data with FEC protection.
     * Each fragment becomes a separate packet in the current FEC group.
     */
    private void sendFragmentedWithFec(ByteBuffer data, long timestamp, long baseFlags) throws IOException {
        int maxFragmentData = MAX_PACKET_SIZE - HEADER_SIZE - SimpleXorFecEncoder.getFecDataHeaderSize() - 4; // extra 4 for fragment index
        int fragmentIndex = 0;

        while (data.hasRemaining()) {
            int chunkSize = Math.min(data.remaining(), maxFragmentData);
            ByteBuffer chunk = ByteBuffer.allocate(chunkSize);
            byte[] temp = new byte[chunkSize];
            data.get(temp);
            chunk.put(temp);
            chunk.flip();

            // Apply FEC to this fragment
            ByteBuffer fecWrapped = fecEncoder.addPacket(chunk);
            sendFecDataPacketFragmented(fecWrapped, timestamp, baseFlags, fragmentIndex++);
        }
    }

    /**
     * Finalize any pending FEC group (call when done sending all frames).
     */
    public void finalizeFec() throws IOException {
        if (fecEnabled && fecEncoder != null && fecEncoder.hasIncompleteGroup()) {
            List<ByteBuffer> parityPackets = fecEncoder.generateParityPackets();
            for (ByteBuffer parity : parityPackets) {
                sendFecParityPacket(parity, lastTimestamp);
            }
            Ln.d("FEC finalized: sent " + parityPackets.size() + " parity packets");
        }
    }

    /**
     * Send a FEC data packet that is part of a fragmented frame.
     */
    private void sendFecDataPacketFragmented(ByteBuffer fecData, long timestamp, long baseFlags, int fragmentIndex) throws IOException {
        long flags = FLAG_FEC_DATA | (1L << 31) | baseFlags; // FEC_DATA + FRAGMENTED + baseFlags (may include KEY_FRAME)
        long sendTimeNs = System.nanoTime();  // Device time when packet is sent
        int dataSize = fecData.remaining();

        // Add 4 bytes for fragment index
        ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + dataSize + 4);
        packet.putInt(sequence++);
        packet.putLong(timestamp);
        packet.putInt((int) flags);
        packet.putLong(sendTimeNs);  // Add send time for E2E latency tracking
        packet.putInt(fragmentIndex);
        packet.put(fecData);
        packet.flip();

        int packetSize = packet.remaining();
        byte[] packetData = packet.array();
        DatagramPacket dp = new DatagramPacket(packetData, packetSize, clientAddress, clientPort);
        socket.send(dp);
        Ln.d("FEC data fragment sent: " + packetSize + " bytes, frag_idx=" + fragmentIndex);
    }
}
