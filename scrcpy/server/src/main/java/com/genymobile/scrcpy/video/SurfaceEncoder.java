package com.genymobile.scrcpy.video;

import com.genymobile.scrcpy.AndroidVersions;
import com.genymobile.scrcpy.AsyncProcessor;
import com.genymobile.scrcpy.Options;
import com.genymobile.scrcpy.device.ConfigurationException;
import com.genymobile.scrcpy.device.Size;
import com.genymobile.scrcpy.device.Streamer;
import com.genymobile.scrcpy.util.Codec;
import com.genymobile.scrcpy.util.CodecOption;
import com.genymobile.scrcpy.util.CodecUtils;
import com.genymobile.scrcpy.util.IO;
import com.genymobile.scrcpy.util.Ln;
import com.genymobile.scrcpy.util.LogUtils;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Surface;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;

public class SurfaceEncoder implements AsyncProcessor {

    private static final int DEFAULT_I_FRAME_INTERVAL = 10; // seconds
    private static final int REPEAT_FRAME_DELAY_US = 100_000; // repeat after 100ms
    private static final String KEY_MAX_FPS_TO_ENCODER = "max-fps-to-encoder";

    // Keep the values in descending order
    private static final int[] MAX_SIZE_FALLBACK = {2560, 1920, 1600, 1280, 1024, 800};
    private static final int MAX_CONSECUTIVE_ERRORS = 3;

    private final SurfaceCapture capture;
    private final Streamer streamer;
    private final String encoderName;
    private final List<CodecOption> codecOptions;
    private final int videoBitRate;
    private final float maxFps;
    private final boolean downsizeOnError;
    private final boolean cbrMode;
    private final float iFrameInterval;

    // Low latency optimization options
    private final boolean lowLatency;
    private final int encoderPriority;
    private final int encoderBuffer;
    private final boolean skipFrames;

    private boolean firstFrameSent;
    private int consecutiveErrors;

    private Thread thread;
    private final AtomicBoolean stopped = new AtomicBoolean();

    // Standby mode support (network mode)
    private final AtomicBoolean standby = new AtomicBoolean(false);
    private final AtomicBoolean singleFrameRequested = new AtomicBoolean(false);
    private final Lock standbyLock = new ReentrantLock();
    private final Condition standbyCondition = standbyLock.newCondition();

    private final CaptureReset reset = new CaptureReset();

    public SurfaceEncoder(SurfaceCapture capture, Streamer streamer, Options options) {
        this.capture = capture;
        this.streamer = streamer;
        this.videoBitRate = options.getVideoBitRate();
        this.maxFps = options.getMaxFps();
        this.codecOptions = options.getVideoCodecOptions();
        this.encoderName = options.getVideoEncoder();
        this.downsizeOnError = options.getDownsizeOnError();
        this.cbrMode = options.isCbrMode();
        this.iFrameInterval = options.getIFrameInterval();
        this.lowLatency = options.isLowLatency();
        this.encoderPriority = options.getEncoderPriority();
        this.encoderBuffer = options.getEncoderBuffer();
        this.skipFrames = options.isSkipFrames();
        Ln.i("SurfaceEncoder initialized: videoBitRate=" + videoBitRate + ", maxFps=" + maxFps
            + ", cbrMode=" + cbrMode + ", iFrameInterval=" + iFrameInterval
            + ", lowLatency=" + lowLatency + ", encoderPriority=" + encoderPriority
            + ", encoderBuffer=" + encoderBuffer + ", skipFrames=" + skipFrames);
    }

    /**
     * Get the CaptureReset instance for requesting sync frames.
     */
    public CaptureReset getCaptureReset() {
        return reset;
    }

    private void streamCapture() throws IOException, ConfigurationException {
        Codec codec = streamer.getCodec();
        MediaCodec mediaCodec = createMediaCodec(codec, encoderName);
        MediaFormat format = createFormat(codec.getMimeType(), videoBitRate, maxFps, cbrMode, iFrameInterval,
            lowLatency, encoderBuffer, codecOptions);

        capture.init(reset);

        try {
            boolean alive;
            boolean headerWritten = false;
            int restartCount = 0;
            Size lastSize = null;

            do {
                reset.consumeReset(); // If a capture reset was requested, it is implicitly fulfilled
                capture.prepare();
                Size size = capture.getSize();

                if (restartCount > 0) {
                    Ln.i("Capture restarted: new size=" + size.getWidth() + "x" + size.getHeight() + " (restart #" + restartCount + ")");
                }

                // Write video header:
                // - First time: always write
                // - On restart: write if size changed (screen rotation)
                if (!headerWritten || (lastSize != null && !lastSize.equals(size))) {
                    streamer.writeVideoHeader(size);
                    headerWritten = true;
                    Ln.d("Video header sent: " + size.getWidth() + "x" + size.getHeight());
                }
                lastSize = size;

                format.setInteger(MediaFormat.KEY_WIDTH, size.getWidth());
                format.setInteger(MediaFormat.KEY_HEIGHT, size.getHeight());

                Surface surface = null;
                boolean mediaCodecStarted = false;
                boolean captureStarted = false;
                try {
                    mediaCodec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
                    surface = mediaCodec.createInputSurface();

                    capture.start(surface);
                    captureStarted = true;

                    mediaCodec.start();
                    mediaCodecStarted = true;

                    // Set the MediaCodec instance to "interrupt" (by signaling an EOS) on reset
                    reset.setRunningMediaCodec(mediaCodec);

                    Ln.d("Encoder configured and started, entering encode loop (size=" + size.getWidth() + "x" + size.getHeight() + ")");

                    if (stopped.get()) {
                        alive = false;
                    } else {
                        boolean resetRequested = reset.consumeReset();
                        if (!resetRequested) {
                            // If a reset is requested during encode(), it will interrupt the encoding by an EOS
                            encode(mediaCodec, streamer);
                        }
                        // The capture might have been closed internally (for example if the camera is disconnected)
                        alive = !stopped.get() && !capture.isClosed();
                    }
                } catch (IllegalStateException | IllegalArgumentException | IOException e) {
                    if (IO.isBrokenPipe(e)) {
                        // Do not retry on broken pipe, which is expected on close because the socket is closed by the client
                        throw e;
                    }
                    Ln.e("Capture/encoding error: " + e.getClass().getName() + ": " + e.getMessage());
                    if (!prepareRetry(size)) {
                        throw e;
                    }
                    alive = true;
                } finally {
                    reset.setRunningMediaCodec(null);
                    if (captureStarted) {
                        capture.stop();
                    }
                    if (mediaCodecStarted) {
                        try {
                            mediaCodec.stop();
                        } catch (IllegalStateException e) {
                            // ignore (just in case)
                        }
                    }
                    mediaCodec.reset();
                    if (surface != null) {
                        surface.release();
                    }
                    restartCount++;
                }
            } while (alive);
        } finally {
            mediaCodec.release();
            capture.release();
        }
    }

    private boolean prepareRetry(Size currentSize) {
        if (firstFrameSent) {
            ++consecutiveErrors;
            if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
                // Definitively fail
                return false;
            }

            // Wait a bit to increase the probability that retrying will fix the problem
            SystemClock.sleep(50);
            return true;
        }

        if (!downsizeOnError) {
            // Must fail immediately
            return false;
        }

        // Downsizing on error is only enabled if an encoding failure occurs before the first frame (downsizing later could be surprising)

        int newMaxSize = chooseMaxSizeFallback(currentSize);
        if (newMaxSize == 0) {
            // Must definitively fail
            return false;
        }

        boolean accepted = capture.setMaxSize(newMaxSize);
        if (!accepted) {
            return false;
        }

        // Retry with a smaller size
        Ln.i("Retrying with -m" + newMaxSize + "...");
        return true;
    }

    private static int chooseMaxSizeFallback(Size failedSize) {
        int currentMaxSize = Math.max(failedSize.getWidth(), failedSize.getHeight());
        for (int value : MAX_SIZE_FALLBACK) {
            if (value < currentMaxSize) {
                // We found a smaller value to reduce the video size
                return value;
            }
        }
        // No fallback, fail definitively
        return 0;
    }

    private void encode(MediaCodec codec, Streamer streamer) throws IOException {
        MediaCodec.BufferInfo bufferInfo = new MediaCodec.BufferInfo();

        boolean eos = false;
        boolean singleFrameMode = false;
        int framesInSingleMode = 0;

        // Use timeout to detect stall and allow periodic checks
        final long DEQUEUE_TIMEOUT_US = 100000; // 100ms timeout
        int consecutiveTimeouts = 0;

        do {
            // Check standby mode before encoding
            if (standby.get() && !singleFrameMode) {
                // Wait in standby mode
                singleFrameMode = waitInStandby();
                if (stopped.get()) {
                    break;
                }
                if (!singleFrameMode) {
                    // Woken up but not for single frame, continue to active mode
                    continue;
                }
                framesInSingleMode = 0;
            }

            int outputBufferId = codec.dequeueOutputBuffer(bufferInfo, DEQUEUE_TIMEOUT_US);
            try {
                // Handle timeout (no buffer available)
                if (outputBufferId < 0) {
                    // INFO_TRY_AGAIN_LATER (-1) means no buffer is available yet
                    // Track consecutive timeouts to detect stalled encoder
                    consecutiveTimeouts++;
                    if (consecutiveTimeouts >= 100) {  // 100 * 100ms = 10 seconds
                        Ln.w("Encoder stall detected: no output for 10 seconds");
                        consecutiveTimeouts = 0;
                    }
                    continue;
                }

                // Reset timeout counter on successful dequeue
                consecutiveTimeouts = 0;

                eos = (bufferInfo.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                if (eos) {
                    Ln.d("EOS received in encode(), exiting loop");
                }
                // On EOS, there might be data or not, depending on bufferInfo.size
                if (outputBufferId >= 0 && bufferInfo.size > 0) {
                    boolean isConfig = (bufferInfo.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;

                    ByteBuffer codecBuffer = codec.getOutputBuffer(outputBufferId);

                    if (!isConfig) {
                        // If this is not a config packet, then it contains a frame
                        firstFrameSent = true;
                        consecutiveErrors = 0;

                        // In single frame mode, count frames and exit after one frame
                        if (singleFrameMode) {
                            framesInSingleMode++;
                            if (framesInSingleMode >= 1) {
                                // Sent one frame, return to standby mode
                                singleFrameMode = false;
                                Ln.d("Single frame sent, returning to standby");
                            }
                        }
                    }

                    streamer.writePacket(codecBuffer, bufferInfo);
                }
            } finally {
                if (outputBufferId >= 0) {
                    codec.releaseOutputBuffer(outputBufferId, false);
                }
            }
        } while (!eos);
    }

    private static MediaCodec createMediaCodec(Codec codec, String encoderName) throws IOException, ConfigurationException {
        if (encoderName != null) {
            Ln.d("Creating encoder by name: '" + encoderName + "'");
            try {
                MediaCodec mediaCodec = MediaCodec.createByCodecName(encoderName);
                String mimeType = Codec.getMimeType(mediaCodec);
                if (!codec.getMimeType().equals(mimeType)) {
                    Ln.e("Video encoder type for \"" + encoderName + "\" (" + mimeType + ") does not match codec type (" + codec.getMimeType() + ")");
                    throw new ConfigurationException("Incorrect encoder type: " + encoderName);
                }
                return mediaCodec;
            } catch (IllegalArgumentException e) {
                Ln.e("Video encoder '" + encoderName + "' for " + codec.getName() + " not found\n" + LogUtils.buildVideoEncoderListMessage());
                throw new ConfigurationException("Unknown encoder: " + encoderName);
            } catch (IOException e) {
                Ln.e("Could not create video encoder '" + encoderName + "' for " + codec.getName() + "\n" + LogUtils.buildVideoEncoderListMessage());
                throw e;
            }
        }

        try {
            MediaCodec mediaCodec = MediaCodec.createEncoderByType(codec.getMimeType());
            Ln.d("Using video encoder: '" + mediaCodec.getName() + "'");
            return mediaCodec;
        } catch (IOException | IllegalArgumentException e) {
            Ln.e("Could not create default video encoder for " + codec.getName() + "\n" + LogUtils.buildVideoEncoderListMessage());
            throw e;
        }
    }

    private static MediaFormat createFormat(String videoMimeType, int bitRate, float maxFps, boolean cbrMode,
                                            float iFrameInterval, boolean lowLatency, int encoderBuffer,
                                            List<CodecOption> codecOptions) {
        MediaFormat format = new MediaFormat();
        format.setString(MediaFormat.KEY_MIME, videoMimeType);
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitRate);

        // Set bitrate mode: CBR for strict control, VBR for variable quality
        // Many hardware encoders (Qualcomm, etc.) ignore KEY_BIT_RATE in VBR mode
        if (Build.VERSION.SDK_INT >= AndroidVersions.API_26_ANDROID_8_0) {
            if (cbrMode) {
                format.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR);
                Ln.i("Using CBR mode for bitrate control");
            } else {
                format.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR);
                Ln.i("Using VBR mode for variable quality");
            }
        }

        Ln.i("Video format created: mimeType=" + videoMimeType + ", bitRate=" + bitRate + ", maxFps=" + maxFps
            + ", iFrameInterval=" + iFrameInterval + ", lowLatency=" + lowLatency + ", encoderBuffer=" + encoderBuffer);

        // must be present to configure the encoder, but does not impact the actual frame rate, which is variable
        format.setInteger(MediaFormat.KEY_FRAME_RATE, 60);
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        if (Build.VERSION.SDK_INT >= AndroidVersions.API_24_ANDROID_7_0) {
            format.setInteger(MediaFormat.KEY_COLOR_RANGE, MediaFormat.COLOR_RANGE_LIMITED);
        }
        format.setFloat(MediaFormat.KEY_I_FRAME_INTERVAL, iFrameInterval);
        // display the very first frame, and recover from bad quality when no new frames
        format.setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, REPEAT_FRAME_DELAY_US); // µs
        if (maxFps > 0) {
            // The key existed privately before Android 10:
            // <https://android.googlesource.com/platform/frameworks/base/+/625f0aad9f7a259b6881006ad8710adce57d1384%5E%21/>
            // <https://github.com/Genymobile/scrcpy/issues/488#issuecomment-567321437>
            format.setFloat(KEY_MAX_FPS_TO_ENCODER, maxFps);
        }

        // Low latency mode (Android 11+)
        // Note: Only set standard keys that are known to work
        if (lowLatency && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
                Ln.i("Low latency mode enabled (KEY_LOW_LATENCY=1)");
            } catch (Exception e) {
                Ln.w("Failed to set KEY_LOW_LATENCY: " + e.getMessage());
            }
        }

        // Disable B-frames for lower latency (B-frames require future frames)
        // This is a standard key that should be safe
        if (encoderBuffer > 0) {
            try {
                format.setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0);
                Ln.i("B-frames disabled for lower latency");
            } catch (Exception e) {
                Ln.d("KEY_MAX_B_FRAMES not supported or not applicable");
            }
        }

        if (codecOptions != null) {
            for (CodecOption option : codecOptions) {
                String key = option.getKey();
                Object value = option.getValue();
                CodecUtils.setCodecOption(format, key, value);
                Ln.d("Video codec option set: " + key + " (" + value.getClass().getSimpleName() + ") = " + value);
            }
        }

        return format;
    }

    @Override
    public void start(TerminationListener listener) {
        thread = new Thread(() -> {
            // Set thread priority based on encoderPriority setting
            // 0 = normal (THREAD_PRIORITY_DEFAULT)
            // 1 = urgent (THREAD_PRIORITY_URGENT_AUDIO)
            // 2 = realtime (THREAD_PRIORITY_URGENT_DISPLAY)
            int priority;
            String priorityName;
            switch (encoderPriority) {
                case 2:
                    priority = android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY;
                    priorityName = "URGENT_DISPLAY";
                    break;
                case 1:
                    priority = android.os.Process.THREAD_PRIORITY_URGENT_AUDIO;
                    priorityName = "URGENT_AUDIO";
                    break;
                default:
                    priority = android.os.Process.THREAD_PRIORITY_DEFAULT;
                    priorityName = "DEFAULT";
                    break;
            }
            android.os.Process.setThreadPriority(priority);
            Ln.i("Encoder thread priority set to: " + priorityName + " (" + priority + ")");

            // Some devices (Meizu) deadlock if the video encoding thread has no Looper
            // <https://github.com/Genymobile/scrcpy/issues/4143>
            Looper.prepare();

            try {
                streamCapture();
            } catch (ConfigurationException e) {
                // Do not print stack trace, a user-friendly error-message has already been logged
            } catch (IOException e) {
                // Broken pipe is expected on close, because the socket is closed by the client
                if (!IO.isBrokenPipe(e)) {
                    Ln.e("Video encoding error", e);
                }
            } finally {
                Ln.d("Screen streaming stopped");
                listener.onTerminated(true);
            }
        }, "video");
        thread.start();
    }

    @Override
    public void stop() {
        if (thread != null) {
            stopped.set(true);
            // Wake up from standby if waiting
            wakeFromStandby();
            reset.reset();
        }
    }

    /**
     * Set standby mode.
     * In standby mode, the encoder is initialized but does not output frames.
     */
    public void setStandby(boolean standby) {
        boolean wasStandby = this.standby.getAndSet(standby);
        if (wasStandby && !standby) {
            // Transitioning from standby to active
            Ln.i("Video encoder: standby -> active");
            wakeFromStandby();
        } else if (!wasStandby && standby) {
            // Transitioning from active to standby
            Ln.i("Video encoder: active -> standby");
        }
    }

    /**
     * Request a single frame to be encoded and sent.
     * Used for screenshot functionality in network mode.
     */
    public void requestSingleFrame() {
        singleFrameRequested.set(true);
        wakeFromStandby();
    }

    /**
     * Wake up the encoder from standby mode.
     */
    private void wakeFromStandby() {
        standbyLock.lock();
        try {
            standbyCondition.signalAll();
        } finally {
            standbyLock.unlock();
        }
    }

    /**
     * Wait while in standby mode (unless single frame is requested).
     * Returns true if a single frame was requested, false if woken up for other reasons.
     */
    private boolean waitInStandby() {
        standbyLock.lock();
        try {
            while (standby.get() && !singleFrameRequested.get() && !stopped.get()) {
                try {
                    standbyCondition.await();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return false;
                }
            }
            return singleFrameRequested.getAndSet(false);
        } finally {
            standbyLock.unlock();
        }
    }

    @Override
    public void join() throws InterruptedException {
        if (thread != null) {
            thread.join();
        }
    }
}
