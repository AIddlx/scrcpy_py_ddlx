package com.genymobile.scrcpy.udp;

import com.genymobile.scrcpy.util.Ln;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;

/**
 * Simple XOR-based FEC (Forward Error Correction) Encoder.
 *
 * Optimized version with minimal memory allocation.
 *
 * FEC Group Structure (K-frame grouping):
 * - Accumulates K frames into one FEC group
 * - When K frames are accumulated, generates parity packets
 * - group_id increments every K frames
 */
public class SimpleXorFecEncoder {

    private final int groupSize;      // K: number of frames per FEC group
    private final int parityCount;    // M: number of parity packets per group

    // Current group state
    private int currentGroupId = 0;
    private int currentFrameIdx = 0;  // Current frame index within group (0 to K-1)

    // Buffer for current frame's data (for XOR parity calculation)
    private ByteArrayOutputStream currentFrameStream = null;

    // List of completed frame data in current group
    private final List<byte[]> frameDataList = new ArrayList<>();

    public SimpleXorFecEncoder(int groupSize, int parityCount) {
        this.groupSize = groupSize;
        this.parityCount = parityCount;
        Ln.i("FEC encoder initialized: K=" + groupSize + ", M=" + parityCount + " (optimized frame-level FEC)");
    }

    public SimpleXorFecEncoder() {
        this(4, 1);  // Default: 4 frames per group, 1 parity
    }

    /**
     * Check if we should finalize the current group (K frames accumulated).
     */
    public boolean shouldFinalizeGroup() {
        return currentFrameIdx >= groupSize;
    }

    /**
     * Get current frame index within the group.
     */
    public int getCurrentFrameIdx() {
        return currentFrameIdx;
    }

    /**
     * Get the group size (K).
     */
    public int getGroupSize() {
        return groupSize;
    }

    /**
     * Add a data packet (fragment) from the current frame.
     * Optimized: uses ByteArrayOutputStream to avoid repeated buffer allocation.
     */
    public ByteBuffer addPacket(ByteBuffer dataPacket) {
        int dataSize = dataPacket.remaining();

        // Lazy init stream
        if (currentFrameStream == null) {
            currentFrameStream = new ByteArrayOutputStream(dataSize);
        }

        // Write data to stream (no copy needed for accumulation)
        if (dataPacket.hasArray()) {
            byte[] arr = dataPacket.array();
            int offset = dataPacket.arrayOffset() + dataPacket.position();
            currentFrameStream.write(arr, offset, dataSize);
        } else {
            byte[] temp = new byte[dataSize];
            dataPacket.get(temp);
            currentFrameStream.write(temp, 0, dataSize);
            dataPacket.position(dataPacket.position() - dataSize);
        }

        // Wrap packet with FEC header using CURRENT FRAME INDEX
        ByteBuffer wrapped = wrapDataPacket(dataPacket, currentFrameIdx, groupSize);

        return wrapped;
    }

    /**
     * Mark the current frame as complete.
     */
    public void frameComplete() {
        if (currentFrameStream != null && currentFrameStream.size() > 0) {
            frameDataList.add(currentFrameStream.toByteArray());
            currentFrameStream.reset();
        }
        currentFrameIdx++;
    }

    /**
     * Check if there are frames waiting in the current group.
     */
    public boolean hasIncompleteGroup() {
        return !frameDataList.isEmpty() || (currentFrameStream != null && currentFrameStream.size() > 0);
    }

    /**
     * Get the number of frames in the current group.
     */
    public int getCurrentGroupSize() {
        return frameDataList.size();
    }

    /**
     * Generate parity packets for the current group.
     */
    public List<ByteBuffer> generateParityPackets() {
        // Note: currentFrameStream is NOT added here because frameComplete() already did it
        // currentFrameStream now contains data for the NEXT frame (after group completed)

        int framesInGroup = frameDataList.size();

        if (framesInGroup == 0) {
            return new ArrayList<>();
        }

        List<ByteBuffer> parityPackets = new ArrayList<>();

        // Find max frame size
        int maxSize = 0;
        for (byte[] frameData : frameDataList) {
            if (frameData.length > maxSize) {
                maxSize = frameData.length;
            }
        }

        Ln.d("FEC generateParity: groupId=" + currentGroupId + ", frames=" + framesInGroup + ", maxSize=" + maxSize);

        // Generate parity by XOR-ing all frame data
        byte[] parity = new byte[maxSize];
        for (byte[] frameData : frameDataList) {
            for (int i = 0; i < frameData.length; i++) {
                parity[i] ^= frameData[i];
            }
        }

        // Wrap with FEC header
        ByteBuffer parityPacket = wrapParityPacket(ByteBuffer.wrap(parity), 0, framesInGroup);
        parityPackets.add(parityPacket);

        // Clear current group and reset for next group
        frameDataList.clear();
        currentFrameIdx = 0;
        currentGroupId++;

        return parityPackets;
    }

    /**
     * Wrap a data packet with FEC header.
     */
    private ByteBuffer wrapDataPacket(ByteBuffer dataPacket, int frameIdx, int totalFrames) {
        int dataSize = dataPacket.remaining();
        ByteBuffer wrapped = ByteBuffer.allocate(7 + dataSize);

        // FEC Header (7 bytes)
        wrapped.putShort((short) currentGroupId);  // group_id
        wrapped.put((byte) frameIdx);               // frame_idx (0 to K-1)
        wrapped.put((byte) totalFrames);            // total_frames (K)
        wrapped.put((byte) parityCount);            // total_parity (M)
        wrapped.putShort((short) dataSize);         // original_size for recovery

        // Payload
        wrapped.put(dataPacket);
        wrapped.flip();

        return wrapped;
    }

    /**
     * Wrap a parity packet with FEC header.
     */
    private ByteBuffer wrapParityPacket(ByteBuffer parityData, int parityIdx, int totalFrames) {
        int dataSize = parityData.remaining();
        ByteBuffer wrapped = ByteBuffer.allocate(5 + dataSize);

        // FEC Header (5 bytes)
        wrapped.putShort((short) currentGroupId);  // group_id
        wrapped.put((byte) parityIdx);              // parity_idx
        wrapped.put((byte) totalFrames);            // total_frames
        wrapped.put((byte) parityCount);            // total_parity (M)

        // Parity data
        wrapped.put(parityData);
        wrapped.flip();

        return wrapped;
    }

    /**
     * Get the FEC header size for data packets.
     */
    public static int getFecDataHeaderSize() {
        return 7;
    }

    /**
     * Get the FEC header size for parity packets.
     */
    public static int getFecParityHeaderSize() {
        return 5;
    }

    /**
     * Get the parity count.
     */
    public int getParityCount() {
        return parityCount;
    }
}
