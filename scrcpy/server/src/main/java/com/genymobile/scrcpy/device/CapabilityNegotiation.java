package com.genymobile.scrcpy.device;

import com.genymobile.scrcpy.video.VideoCodec;
import com.genymobile.scrcpy.audio.AudioCodec;

import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

/**
 * Capability negotiation protocol constants and utilities.
 *
 * Client-side constants are defined in: scrcpy_py_ddlx/core/negotiation.py
 *
 * Protocol version: 1.0
 */
public final class CapabilityNegotiation {

    // Protocol version
    public static final int PROTOCOL_VERSION = 1;

    // Video codec IDs (4-byte ASCII)
    public static final int VIDEO_CODEC_H264 = 0x68323634; // "h264"
    public static final int VIDEO_CODEC_H265 = 0x68323635; // "h265"
    public static final int VIDEO_CODEC_AV1 = 0x00617631;  // "av1"

    // Audio codec IDs
    public static final int AUDIO_CODEC_OPUS = 0x6f707573; // "opus"
    public static final int AUDIO_CODEC_AAC = 0x00000003;
    public static final int AUDIO_CODEC_FLAC = 0x00000004;

    // Encoder flags
    public static final int ENCODER_FLAG_HARDWARE = 0x01;
    public static final int ENCODER_FLAG_SOFTWARE = 0x02;

    // Client config flags
    public static final int CONFIG_AUDIO_ENABLED = 0x01;
    public static final int CONFIG_VIDEO_ENABLED = 0x02;
    public static final int CONFIG_CBR_MODE = 0x04;
    public static final int CONFIG_VIDEO_FEC = 0x08;
    public static final int CONFIG_AUDIO_FEC = 0x10;

    // Encoder info struct
    public static class EncoderInfo {
        public final int codecId;
        public final int flags;
        public final int priority;

        public EncoderInfo(int codecId, int flags, int priority) {
            this.codecId = codecId;
            this.flags = flags;
            this.priority = priority;
        }

        public boolean isHardware() {
            return (flags & ENCODER_FLAG_HARDWARE) != 0;
        }

        public boolean isSoftware() {
            return (flags & ENCODER_FLAG_SOFTWARE) != 0;
        }
    }

    // Client configuration struct
    public static class ClientConfig {
        public final int videoCodecId;
        public final int audioCodecId;
        public final int videoBitrate;
        public final int audioBitrate;
        public final int maxFps;
        public final int configFlags;
        public final float iFrameInterval;

        public ClientConfig(int videoCodecId, int audioCodecId, int videoBitrate,
                           int audioBitrate, int maxFps, int configFlags, float iFrameInterval) {
            this.videoCodecId = videoCodecId;
            this.audioCodecId = audioCodecId;
            this.videoBitrate = videoBitrate;
            this.audioBitrate = audioBitrate;
            this.maxFps = maxFps;
            this.configFlags = configFlags;
            this.iFrameInterval = iFrameInterval;
        }

        public boolean isAudioEnabled() {
            return (configFlags & CONFIG_AUDIO_ENABLED) != 0;
        }

        public boolean isVideoEnabled() {
            return (configFlags & CONFIG_VIDEO_ENABLED) != 0;
        }

        public boolean isCbrMode() {
            return (configFlags & CONFIG_CBR_MODE) != 0;
        }

        public boolean isVideoFecEnabled() {
            return (configFlags & CONFIG_VIDEO_FEC) != 0;
        }

        public boolean isAudioFecEnabled() {
            return (configFlags & CONFIG_AUDIO_FEC) != 0;
        }

        public VideoCodec getVideoCodec() {
            if (videoCodecId == VIDEO_CODEC_H264) return VideoCodec.H264;
            if (videoCodecId == VIDEO_CODEC_H265) return VideoCodec.H265;
            if (videoCodecId == VIDEO_CODEC_AV1) return VideoCodec.AV1;
            return VideoCodec.H264; // default
        }

        public AudioCodec getAudioCodec() {
            if (audioCodecId == AUDIO_CODEC_OPUS) return AudioCodec.OPUS;
            if (audioCodecId == AUDIO_CODEC_AAC) return AudioCodec.AAC;
            if (audioCodecId == AUDIO_CODEC_FLAC) return AudioCodec.FLAC;
            return AudioCodec.OPUS; // default
        }
    }

    private CapabilityNegotiation() {
        // not instantiable
    }

    /**
     * Query available video encoders.
     *
     * @return List of encoder info with codec ID, flags, and priority
     */
    public static List<EncoderInfo> getVideoEncoders() {
        List<EncoderInfo> encoders = new ArrayList<>();
        MediaCodecList codecList = new MediaCodecList(MediaCodecList.REGULAR_CODECS);

        for (MediaCodecInfo info : codecList.getCodecInfos()) {
            if (info.isEncoder()) {
                String[] types = info.getSupportedTypes();
                for (String type : types) {
                    int codecId = getVideoCodecId(type);
                    if (codecId != 0) {
                        int flags = 0;
                        // Check if hardware encoder
                        if (!info.getName().startsWith("OMX.google.") &&
                            !info.getName().startsWith("c2.android.")) {
                            flags |= ENCODER_FLAG_HARDWARE;
                        } else {
                            flags |= ENCODER_FLAG_SOFTWARE;
                        }

                        // Priority: hardware encoders first, then by codec efficiency
                        int priority = calculatePriority(codecId, flags);

                        encoders.add(new EncoderInfo(codecId, flags, priority));
                    }
                }
            }
        }

        // Sort by priority (lower is better)
        encoders.sort((a, b) -> Integer.compare(a.priority, b.priority));

        return encoders;
    }

    /**
     * Query available audio encoders.
     */
    public static List<EncoderInfo> getAudioEncoders() {
        List<EncoderInfo> encoders = new ArrayList<>();
        MediaCodecList codecList = new MediaCodecList(MediaCodecList.REGULAR_CODECS);

        for (MediaCodecInfo info : codecList.getCodecInfos()) {
            if (info.isEncoder()) {
                String[] types = info.getSupportedTypes();
                for (String type : types) {
                    int codecId = getAudioCodecId(type);
                    if (codecId != 0) {
                        int flags = ENCODER_FLAG_SOFTWARE; // Audio encoders are typically software
                        int priority = calculateAudioPriority(codecId);
                        encoders.add(new EncoderInfo(codecId, flags, priority));
                    }
                }
            }
        }

        encoders.sort((a, b) -> Integer.compare(a.priority, b.priority));
        return encoders;
    }

    private static int getVideoCodecId(String mimeType) {
        switch (mimeType) {
            case MediaFormat.MIMETYPE_VIDEO_AVC:
                return VIDEO_CODEC_H264;
            case MediaFormat.MIMETYPE_VIDEO_HEVC:
                return VIDEO_CODEC_H265;
            case MediaFormat.MIMETYPE_VIDEO_AV1:
                return VIDEO_CODEC_AV1;
            default:
                return 0;
        }
    }

    private static int getAudioCodecId(String mimeType) {
        switch (mimeType) {
            case MediaFormat.MIMETYPE_AUDIO_OPUS:
                return AUDIO_CODEC_OPUS;
            case MediaFormat.MIMETYPE_AUDIO_AAC:
                return AUDIO_CODEC_AAC;
            case MediaFormat.MIMETYPE_AUDIO_FLAC:
                return AUDIO_CODEC_FLAC;
            default:
                return 0;
        }
    }

    private static int calculatePriority(int codecId, int flags) {
        // Lower priority = better
        // Hardware encoders get -100 bonus
        int base = (flags & ENCODER_FLAG_HARDWARE) != 0 ? 0 : 100;

        switch (codecId) {
            case VIDEO_CODEC_AV1:
                return base + 0;  // Best
            case VIDEO_CODEC_H265:
                return base + 10;
            case VIDEO_CODEC_H264:
                return base + 20;
            default:
                return base + 100;
        }
    }

    private static int calculateAudioPriority(int codecId) {
        switch (codecId) {
            case AUDIO_CODEC_OPUS:
                return 0;  // Best
            case AUDIO_CODEC_AAC:
                return 10;
            case AUDIO_CODEC_FLAC:
                return 20;
            default:
                return 100;
        }
    }

    /**
     * Send device capabilities to client.
     *
     * Note: Device name is sent separately via sendDeviceMeta before this call.
     *
     * Format:
     * - screen_width: 4 bytes (uint32, big-endian)
     * - screen_height: 4 bytes (uint32, big-endian)
     * - video_encoder_count: 1 byte
     * - video_encoders: N * 12 bytes (codec_id:4, flags:4, priority:4)
     * - audio_encoder_count: 1 byte
     * - audio_encoders: M * 12 bytes
     */
    public static void sendCapabilities(OutputStream output,
                                        int screenWidth, int screenHeight) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(4096);
        buffer.order(ByteOrder.BIG_ENDIAN);

        // Screen dimensions
        buffer.putInt(screenWidth);
        buffer.putInt(screenHeight);

        // Video encoders
        List<EncoderInfo> videoEncoders = getVideoEncoders();
        buffer.put((byte) videoEncoders.size());
        for (EncoderInfo encoder : videoEncoders) {
            buffer.putInt(encoder.codecId);
            buffer.putInt(encoder.flags);
            buffer.putInt(encoder.priority);
        }

        // Audio encoders
        List<EncoderInfo> audioEncoders = getAudioEncoders();
        buffer.put((byte) audioEncoders.size());
        for (EncoderInfo encoder : audioEncoders) {
            buffer.putInt(encoder.codecId);
            buffer.putInt(encoder.flags);
            buffer.putInt(encoder.priority);
        }

        // Send
        output.write(buffer.array(), 0, buffer.position());
        output.flush();
    }

    /**
     * Parse client configuration from bytes.
     *
     * Format:
     * - video_codec_id: 4 bytes (uint32, big-endian)
     * - audio_codec_id: 4 bytes (uint32, big-endian)
     * - video_bitrate: 4 bytes (uint32, big-endian)
     * - audio_bitrate: 4 bytes (uint32, big-endian)
     * - max_fps: 4 bytes (uint32, big-endian)
     * - config_flags: 4 bytes (uint32, big-endian)
     * - reserved: 4 bytes
     * - i_frame_interval: 4 bytes (IEEE 754 float, big-endian)
     */
    public static ClientConfig parseClientConfig(byte[] data) {
        ByteBuffer buffer = ByteBuffer.wrap(data);
        buffer.order(ByteOrder.BIG_ENDIAN);

        int videoCodecId = buffer.getInt();
        int audioCodecId = buffer.getInt();
        int videoBitrate = buffer.getInt();
        int audioBitrate = buffer.getInt();
        int maxFps = buffer.getInt();
        int configFlags = buffer.getInt();
        buffer.getInt(); // reserved
        float iFrameInterval = buffer.getFloat();

        return new ClientConfig(videoCodecId, audioCodecId, videoBitrate,
                               audioBitrate, maxFps, configFlags, iFrameInterval);
    }
}
