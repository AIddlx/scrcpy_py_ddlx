package com.genymobile.scrcpy;

import com.genymobile.scrcpy.audio.AudioCodec;
import com.genymobile.scrcpy.audio.AudioSource;
import com.genymobile.scrcpy.device.CapabilityNegotiation;
import com.genymobile.scrcpy.device.Device;
import com.genymobile.scrcpy.device.NewDisplay;
import com.genymobile.scrcpy.device.Orientation;
import com.genymobile.scrcpy.device.Size;
import com.genymobile.scrcpy.util.CodecOption;
import com.genymobile.scrcpy.util.Ln;
import com.genymobile.scrcpy.video.CameraAspectRatio;
import com.genymobile.scrcpy.video.CameraFacing;
import com.genymobile.scrcpy.video.VideoCodec;
import com.genymobile.scrcpy.video.VideoSource;
import com.genymobile.scrcpy.wrappers.WindowManager;

import android.graphics.Rect;
import android.util.Pair;

import java.util.List;
import java.util.Locale;

public class Options {

    private Ln.Level logLevel = Ln.Level.DEBUG;
    private int scid = -1; // 31-bit non-negative value, or -1
    private boolean video = true;
    private boolean audio = true;
    private int maxSize;
    private VideoCodec videoCodec = VideoCodec.H264;
    private AudioCodec audioCodec = AudioCodec.OPUS;
    private VideoSource videoSource = VideoSource.DISPLAY;
    private AudioSource audioSource = AudioSource.OUTPUT;
    private boolean audioDup;
    private int videoBitRate = 8000000;
    private int audioBitRate = 128000;
    private float maxFps;
    private float angle;
    private boolean tunnelForward;
    private Rect crop;
    private boolean control = true;
    private int displayId;
    private String cameraId;
    private Size cameraSize;
    private CameraFacing cameraFacing;
    private CameraAspectRatio cameraAspectRatio;
    private int cameraFps;
    private boolean cameraHighSpeed;
    private boolean showTouches;
    private boolean stayAwake;
    private int screenOffTimeout = -1;
    private int displayImePolicy = -1;
    private List<CodecOption> videoCodecOptions;
    private List<CodecOption> audioCodecOptions;

    private String videoEncoder;
    private String audioEncoder;
    private boolean powerOffScreenOnClose;
    private boolean clipboardAutosync = true;
    private boolean downsizeOnError = true;
    private boolean cleanup = true;
    private boolean powerOn = true;

    private NewDisplay newDisplay;
    private boolean vdDestroyContent = true;
    private boolean vdSystemDecorations = true;

    private Orientation.Lock captureOrientationLock = Orientation.Lock.Unlocked;
    private Orientation captureOrientation = Orientation.Orient0;

    private boolean listEncoders;
    private boolean listDisplays;
    private boolean listCameras;
    private boolean listCameraSizes;
    private boolean listApps;

    // Network mode ports (0 = ADB tunnel mode)
    private int controlPort = 0;
    private int videoPort = 27185;
    private int audioPort = 27186;
    private int filePort = 27187;  // File transfer port (TCP)
    private int discoveryPort = 27183;

    // FEC (Forward Error Correction) for UDP mode
    private boolean fecEnabled = false;      // Legacy: enable FEC for both video and audio
    private boolean videoFecEnabled = false; // Enable FEC for video stream
    private boolean audioFecEnabled = false; // Enable FEC for audio stream
    private int fecGroupSize = 4;            // K: data packets per group
    private int fecParityCount = 1;          // M: parity packets per group
    private String fecMode = "frame";        // FEC mode: "frame" or "fragment"

    // Bitrate mode: "cbr" (constant) or "vbr" (variable, default)
    private String bitrateMode = "vbr";

    // I-frame interval in seconds (default 10, lower = faster recovery but more bandwidth)
    // Supports floating point values (e.g., 0.5 for half a second)
    private float iFrameInterval = 10;

    // Hot-connection (stay-alive) mode
    private boolean stayAlive = false;      // Keep server running after disconnect
    private int maxConnections = -1;        // Max connections before exit (-1 = unlimited)

    // Low latency optimization options
    // Note: lowLatency may cause configure() to fail on some devices
    private boolean lowLatency = false;     // Enable MediaCodec low latency mode (Android 11+)
    private int encoderPriority = 1;        // Encoder thread priority: 0=normal, 1=urgent, 2=realtime
    private int encoderBuffer = 0;          // Encoder buffer: 0=auto, 1=disable B-frames
    private boolean skipFrames = true;      // Skip buffered frames to reduce latency (default: enabled)

    // Options not used by the scrcpy client, but useful to use scrcpy-server directly
    private boolean sendDeviceMeta = true; // send device name and size
    private boolean sendFrameMeta = true; // send PTS so that the client may record properly
    private boolean sendDummyByte = true; // write a byte on start to detect connection issues
    private boolean sendCodecMeta = true; // write the codec metadata before the stream

    public Ln.Level getLogLevel() {
        return logLevel;
    }

    public int getScid() {
        return scid;
    }

    public boolean getVideo() {
        return video;
    }

    public boolean getAudio() {
        return audio;
    }

    public int getMaxSize() {
        return maxSize;
    }

    public VideoCodec getVideoCodec() {
        return videoCodec;
    }

    public AudioCodec getAudioCodec() {
        return audioCodec;
    }

    public VideoSource getVideoSource() {
        return videoSource;
    }

    public AudioSource getAudioSource() {
        return audioSource;
    }

    public boolean getAudioDup() {
        return audioDup;
    }

    public int getVideoBitRate() {
        return videoBitRate;
    }

    public int getAudioBitRate() {
        return audioBitRate;
    }

    public float getMaxFps() {
        return maxFps;
    }

    public float getAngle() {
        return angle;
    }

    public boolean isTunnelForward() {
        return tunnelForward;
    }

    public Rect getCrop() {
        return crop;
    }

    public boolean getControl() {
        return control;
    }

    public int getDisplayId() {
        return displayId;
    }

    public String getCameraId() {
        return cameraId;
    }

    public Size getCameraSize() {
        return cameraSize;
    }

    public CameraFacing getCameraFacing() {
        return cameraFacing;
    }

    public CameraAspectRatio getCameraAspectRatio() {
        return cameraAspectRatio;
    }

    public int getCameraFps() {
        return cameraFps;
    }

    public boolean getCameraHighSpeed() {
        return cameraHighSpeed;
    }

    public boolean getShowTouches() {
        return showTouches;
    }

    public boolean getStayAwake() {
        return stayAwake;
    }

    public int getScreenOffTimeout() {
        return screenOffTimeout;
    }

    public int getDisplayImePolicy() {
        return displayImePolicy;
    }

    public List<CodecOption> getVideoCodecOptions() {
        return videoCodecOptions;
    }

    public List<CodecOption> getAudioCodecOptions() {
        return audioCodecOptions;
    }

    public String getVideoEncoder() {
        return videoEncoder;
    }

    public String getAudioEncoder() {
        return audioEncoder;
    }

    public boolean getPowerOffScreenOnClose() {
        return this.powerOffScreenOnClose;
    }

    public boolean getClipboardAutosync() {
        return clipboardAutosync;
    }

    public boolean getDownsizeOnError() {
        return downsizeOnError;
    }

    public boolean getCleanup() {
        return cleanup;
    }

    public boolean getPowerOn() {
        return powerOn;
    }

    public NewDisplay getNewDisplay() {
        return newDisplay;
    }

    public Orientation getCaptureOrientation() {
        return captureOrientation;
    }

    public Orientation.Lock getCaptureOrientationLock() {
        return captureOrientationLock;
    }

    public boolean getVDDestroyContent() {
        return vdDestroyContent;
    }

    public boolean getVDSystemDecorations() {
        return vdSystemDecorations;
    }

    public boolean getList() {
        return listEncoders || listDisplays || listCameras || listCameraSizes || listApps;
    }

    public boolean getListEncoders() {
        return listEncoders;
    }

    public boolean getListDisplays() {
        return listDisplays;
    }

    public boolean getListCameras() {
        return listCameras;
    }

    public boolean getListCameraSizes() {
        return listCameraSizes;
    }

    public boolean getListApps() {
        return listApps;
    }

    public boolean getSendDeviceMeta() {
        return sendDeviceMeta;
    }

    public boolean getSendFrameMeta() {
        return sendFrameMeta;
    }

    public boolean getSendDummyByte() {
        return sendDummyByte;
    }

    public boolean getSendCodecMeta() {
        return sendCodecMeta;
    }

    public int getControlPort() {
        return controlPort;
    }

    public int getVideoPort() {
        return videoPort;
    }

    public int getAudioPort() {
        return audioPort;
    }

    public int getFilePort() {
        return filePort;
    }

    public int getDiscoveryPort() {
        return discoveryPort;
    }

    public boolean isNetworkMode() {
        return controlPort > 0;
    }

    public boolean isFecEnabled() {
        return fecEnabled;
    }

    public boolean isVideoFecEnabled() {
        // Video FEC enabled if: video_fec_enabled=true OR (fec_enabled=true and video_fec_enabled not explicitly set)
        return videoFecEnabled || (fecEnabled && !videoFecEnabled && !audioFecEnabled);
    }

    public boolean isAudioFecEnabled() {
        // Audio FEC enabled if: audio_fec_enabled=true OR (fec_enabled=true and audio_fec_enabled not explicitly set)
        return audioFecEnabled || (fecEnabled && !videoFecEnabled && !audioFecEnabled);
    }

    public int getFecGroupSize() {
        return fecGroupSize;
    }

    public int getFecParityCount() {
        return fecParityCount;
    }

    public String getBitrateMode() {
        return bitrateMode;
    }

    public boolean isCbrMode() {
        return "cbr".equalsIgnoreCase(bitrateMode);
    }

    public float getIFrameInterval() {
        return iFrameInterval;
    }

    public void setIFrameInterval(float iFrameInterval) {
        this.iFrameInterval = iFrameInterval;
    }

    public boolean isStayAlive() {
        return stayAlive;
    }

    public int getMaxConnections() {
        return maxConnections;
    }

    public boolean isLowLatency() {
        return lowLatency;
    }

    public int getEncoderPriority() {
        return encoderPriority;
    }

    public int getEncoderBuffer() {
        return encoderBuffer;
    }

    public boolean isSkipFrames() {
        return skipFrames;
    }

    public void setFecEnabled(boolean fecEnabled) {
        this.fecEnabled = fecEnabled;
    }

    public void setVideoFecEnabled(boolean videoFecEnabled) {
        this.videoFecEnabled = videoFecEnabled;
    }

    public void setAudioFecEnabled(boolean audioFecEnabled) {
        this.audioFecEnabled = audioFecEnabled;
    }

    public void setFecGroupSize(int fecGroupSize) {
        this.fecGroupSize = fecGroupSize;
    }

    public void setFecParityCount(int fecParityCount) {
        this.fecParityCount = fecParityCount;
    }

    public String getFecMode() {
        return fecMode;
    }

    public void setFecMode(String fecMode) {
        this.fecMode = fecMode;
    }

    @SuppressWarnings("MethodLength")
    public static Options parse(String... args) {
        if (args.length < 1) {
            throw new IllegalArgumentException("Missing client version");
        }

        String clientVersion = args[0];
        if (!clientVersion.equals(BuildConfig.VERSION_NAME)) {
            throw new IllegalArgumentException(
                    "The server version (" + BuildConfig.VERSION_NAME + ") does not match the client " + "(" + clientVersion + ")");
        }

        Options options = new Options();

        for (int i = 1; i < args.length; ++i) {
            String arg = args[i];
            int equalIndex = arg.indexOf('=');
            if (equalIndex == -1) {
                throw new IllegalArgumentException("Invalid key=value pair: \"" + arg + "\"");
            }
            String key = arg.substring(0, equalIndex);
            String value = arg.substring(equalIndex + 1);
            switch (key) {
                case "scid":
                    int scid = Integer.parseInt(value, 0x10);
                    if (scid < -1) {
                        throw new IllegalArgumentException("scid may not be negative (except -1 for 'none'): " + scid);
                    }
                    options.scid = scid;
                    break;
                case "log_level":
                    options.logLevel = Ln.Level.valueOf(value.toUpperCase(Locale.ENGLISH));
                    break;
                case "video":
                    options.video = Boolean.parseBoolean(value);
                    break;
                case "audio":
                    options.audio = Boolean.parseBoolean(value);
                    break;
                case "video_codec":
                    VideoCodec videoCodec = VideoCodec.findByName(value);
                    if (videoCodec == null) {
                        throw new IllegalArgumentException("Video codec " + value + " not supported");
                    }
                    options.videoCodec = videoCodec;
                    break;
                case "audio_codec":
                    AudioCodec audioCodec = AudioCodec.findByName(value);
                    if (audioCodec == null) {
                        throw new IllegalArgumentException("Audio codec " + value + " not supported");
                    }
                    options.audioCodec = audioCodec;
                    break;
                case "video_source":
                    VideoSource videoSource = VideoSource.findByName(value);
                    if (videoSource == null) {
                        throw new IllegalArgumentException("Video source " + value + " not supported");
                    }
                    options.videoSource = videoSource;
                    break;
                case "audio_source":
                    AudioSource audioSource = AudioSource.findByName(value);
                    if (audioSource == null) {
                        throw new IllegalArgumentException("Audio source " + value + " not supported");
                    }
                    options.audioSource = audioSource;
                    break;
                case "audio_dup":
                    options.audioDup = Boolean.parseBoolean(value);
                    break;
                case "max_size":
                    options.maxSize = Integer.parseInt(value) & ~7; // multiple of 8
                    break;
                case "video_bit_rate":
                    options.videoBitRate = Integer.parseInt(value);
                    Ln.i("Parsed video_bit_rate: " + options.videoBitRate);
                    break;
                case "audio_bit_rate":
                    options.audioBitRate = Integer.parseInt(value);
                    break;
                case "max_fps":
                    options.maxFps = parseFloat("max_fps", value);
                    break;
                case "angle":
                    options.angle = parseFloat("angle", value);
                    break;
                case "tunnel_forward":
                    options.tunnelForward = Boolean.parseBoolean(value);
                    break;
                case "crop":
                    if (!value.isEmpty()) {
                        options.crop = parseCrop(value);
                    }
                    break;
                case "control":
                    options.control = Boolean.parseBoolean(value);
                    break;
                case "display_id":
                    options.displayId = Integer.parseInt(value);
                    break;
                case "show_touches":
                    options.showTouches = Boolean.parseBoolean(value);
                    break;
                case "stay_awake":
                    options.stayAwake = Boolean.parseBoolean(value);
                    break;
                case "screen_off_timeout":
                    options.screenOffTimeout = Integer.parseInt(value);
                    if (options.screenOffTimeout < -1) {
                        throw new IllegalArgumentException("Invalid screen off timeout: " + options.screenOffTimeout);
                    }
                    break;
                case "video_codec_options":
                    options.videoCodecOptions = CodecOption.parse(value);
                    break;
                case "audio_codec_options":
                    options.audioCodecOptions = CodecOption.parse(value);
                    break;
                case "video_encoder":
                    if (!value.isEmpty()) {
                        options.videoEncoder = value;
                    }
                    break;
                case "audio_encoder":
                    if (!value.isEmpty()) {
                        options.audioEncoder = value;
                    }
                case "power_off_on_close":
                    options.powerOffScreenOnClose = Boolean.parseBoolean(value);
                    break;
                case "clipboard_autosync":
                    options.clipboardAutosync = Boolean.parseBoolean(value);
                    break;
                case "downsize_on_error":
                    options.downsizeOnError = Boolean.parseBoolean(value);
                    break;
                case "cleanup":
                    options.cleanup = Boolean.parseBoolean(value);
                    break;
                case "power_on":
                    options.powerOn = Boolean.parseBoolean(value);
                    break;
                case "list_encoders":
                    options.listEncoders = Boolean.parseBoolean(value);
                    break;
                case "list_displays":
                    options.listDisplays = Boolean.parseBoolean(value);
                    break;
                case "list_cameras":
                    options.listCameras = Boolean.parseBoolean(value);
                    break;
                case "list_camera_sizes":
                    options.listCameraSizes = Boolean.parseBoolean(value);
                    break;
                case "list_apps":
                    options.listApps = Boolean.parseBoolean(value);
                    break;
                case "camera_id":
                    if (!value.isEmpty()) {
                        options.cameraId = value;
                    }
                    break;
                case "camera_size":
                    if (!value.isEmpty()) {
                        options.cameraSize = parseSize(value);
                    }
                    break;
                case "camera_facing":
                    if (!value.isEmpty()) {
                        CameraFacing facing = CameraFacing.findByName(value);
                        if (facing == null) {
                            throw new IllegalArgumentException("Camera facing " + value + " not supported");
                        }
                        options.cameraFacing = facing;
                    }
                    break;
                case "camera_ar":
                    if (!value.isEmpty()) {
                        options.cameraAspectRatio = parseCameraAspectRatio(value);
                    }
                    break;
                case "camera_fps":
                    options.cameraFps = Integer.parseInt(value);
                    break;
                case "camera_high_speed":
                    options.cameraHighSpeed = Boolean.parseBoolean(value);
                    break;
                case "new_display":
                    options.newDisplay = parseNewDisplay(value);
                    break;
                case "vd_destroy_content":
                    options.vdDestroyContent = Boolean.parseBoolean(value);
                    break;
                case "vd_system_decorations":
                    options.vdSystemDecorations = Boolean.parseBoolean(value);
                    break;
                case "capture_orientation":
                    Pair<Orientation.Lock, Orientation> pair = parseCaptureOrientation(value);
                    options.captureOrientationLock = pair.first;
                    options.captureOrientation = pair.second;
                    break;
                case "display_ime_policy":
                    options.displayImePolicy = parseDisplayImePolicy(value);
                    break;
                case "send_device_meta":
                    options.sendDeviceMeta = Boolean.parseBoolean(value);
                    break;
                case "send_frame_meta":
                    options.sendFrameMeta = Boolean.parseBoolean(value);
                    break;
                case "send_dummy_byte":
                    options.sendDummyByte = Boolean.parseBoolean(value);
                    break;
                case "send_codec_meta":
                    options.sendCodecMeta = Boolean.parseBoolean(value);
                    break;
                case "raw_stream":
                    boolean rawStream = Boolean.parseBoolean(value);
                    if (rawStream) {
                        options.sendDeviceMeta = false;
                        options.sendFrameMeta = false;
                        options.sendDummyByte = false;
                        options.sendCodecMeta = false;
                    }
                    break;
                case "control_port":
                    options.controlPort = Integer.parseInt(value);
                    break;
                case "video_port":
                    options.videoPort = Integer.parseInt(value);
                    break;
                case "audio_port":
                    options.audioPort = Integer.parseInt(value);
                    break;
                case "file_port":
                    options.filePort = Integer.parseInt(value);
                    break;
                case "discovery_port":
                    options.discoveryPort = Integer.parseInt(value);
                    break;
                case "fec_enabled":
                    options.fecEnabled = Boolean.parseBoolean(value);
                    break;
                case "video_fec_enabled":
                    options.videoFecEnabled = Boolean.parseBoolean(value);
                    break;
                case "audio_fec_enabled":
                    options.audioFecEnabled = Boolean.parseBoolean(value);
                    break;
                case "fec_group_size":
                    options.fecGroupSize = Integer.parseInt(value);
                    break;
                case "fec_parity_count":
                    options.fecParityCount = Integer.parseInt(value);
                    break;
                case "fec_mode":
                    options.fecMode = value.toLowerCase(Locale.ENGLISH);
                    break;
                case "bitrate_mode":
                    options.bitrateMode = value.toLowerCase(Locale.ENGLISH);
                    break;
                case "i_frame_interval":
                    options.iFrameInterval = Float.parseFloat(value);
                    Ln.i("Parsed i_frame_interval: " + options.iFrameInterval + " seconds");
                    break;
                case "stay_alive":
                    options.stayAlive = Boolean.parseBoolean(value);
                    Ln.i("Stay-alive mode: " + options.stayAlive);
                    break;
                case "max_connections":
                    options.maxConnections = Integer.parseInt(value);
                    Ln.i("Max connections: " + options.maxConnections);
                    break;
                case "low_latency":
                    options.lowLatency = Boolean.parseBoolean(value);
                    Ln.i("Low latency mode: " + options.lowLatency);
                    break;
                case "encoder_priority":
                    options.encoderPriority = Integer.parseInt(value);
                    Ln.i("Encoder priority: " + options.encoderPriority);
                    break;
                case "encoder_buffer":
                    options.encoderBuffer = Integer.parseInt(value);
                    Ln.i("Encoder buffer frames: " + options.encoderBuffer);
                    break;
                case "skip_frames":
                    options.skipFrames = Boolean.parseBoolean(value);
                    Ln.i("Skip frames mode: " + options.skipFrames);
                    break;
                default:
                    Ln.w("Unknown server option: " + key);
                    break;
            }
        }

        if (options.newDisplay != null) {
            assert options.displayId == 0 : "Must not set both displayId and newDisplay";
            options.displayId = Device.DISPLAY_ID_NONE;
        }

        return options;
    }

    private static Rect parseCrop(String crop) {
        // input format: "width:height:x:y"
        String[] tokens = crop.split(":");
        if (tokens.length != 4) {
            throw new IllegalArgumentException("Crop must contains 4 values separated by colons: \"" + crop + "\"");
        }
        int width = Integer.parseInt(tokens[0]);
        int height = Integer.parseInt(tokens[1]);
        if (width <= 0 || height <= 0) {
            throw new IllegalArgumentException("Invalid crop size: " + width + "x" + height);
        }
        int x = Integer.parseInt(tokens[2]);
        int y = Integer.parseInt(tokens[3]);
        if (x < 0 || y < 0) {
            throw new IllegalArgumentException("Invalid crop offset: " + x + ":" + y);
        }
        return new Rect(x, y, x + width, y + height);
    }

    private static Size parseSize(String size) {
        // input format: "<width>x<height>"
        String[] tokens = size.split("x");
        if (tokens.length != 2) {
            throw new IllegalArgumentException("Invalid size format (expected <width>x<height>): \"" + size + "\"");
        }
        int width = Integer.parseInt(tokens[0]);
        int height = Integer.parseInt(tokens[1]);
        if (width <= 0 || height <= 0) {
            throw new IllegalArgumentException("Invalid non-positive size dimension: \"" + size + "\"");
        }
        return new Size(width, height);
    }

    private static CameraAspectRatio parseCameraAspectRatio(String ar) {
        if ("sensor".equals(ar)) {
            return CameraAspectRatio.sensorAspectRatio();
        }

        String[] tokens = ar.split(":");
        if (tokens.length == 2) {
            int w = Integer.parseInt(tokens[0]);
            int h = Integer.parseInt(tokens[1]);
            return CameraAspectRatio.fromFraction(w, h);
        }

        float floatAr = Float.parseFloat(tokens[0]);
        return CameraAspectRatio.fromFloat(floatAr);
    }

    private static float parseFloat(String key, String value) {
        try {
            return Float.parseFloat(value);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Invalid float value for " + key + ": \"" + value + "\"");
        }
    }

    private static NewDisplay parseNewDisplay(String newDisplay) {
        // Possible inputs:
        //  - "" (empty string)
        //  - "<width>x<height>/<dpi>"
        //  - "<width>x<height>"
        //  - "/<dpi>"
        if (newDisplay.isEmpty()) {
            return new NewDisplay();
        }

        String[] tokens = newDisplay.split("/");

        Size size;
        if (!tokens[0].isEmpty()) {
            size = parseSize(tokens[0]);
        } else {
            size = null;
        }

        int dpi;
        if (tokens.length >= 2) {
            dpi = Integer.parseInt(tokens[1]);
            if (dpi <= 0) {
                throw new IllegalArgumentException("Invalid non-positive dpi: " + tokens[1]);
            }
        } else {
            dpi = 0;
        }

        return new NewDisplay(size, dpi);
    }

    private static Pair<Orientation.Lock, Orientation> parseCaptureOrientation(String value) {
        if (value.isEmpty()) {
            throw new IllegalArgumentException("Empty capture orientation string");
        }

        Orientation.Lock lock;
        if (value.charAt(0) == '@') {
            // Consume '@'
            value = value.substring(1);
            if (value.isEmpty()) {
                // Only '@': lock to the initial orientation (orientation is unused)
                return Pair.create(Orientation.Lock.LockedInitial, Orientation.Orient0);
            }
            lock = Orientation.Lock.LockedValue;
        } else {
            lock = Orientation.Lock.Unlocked;
        }

        return Pair.create(lock, Orientation.getByName(value));
    }

    private static int parseDisplayImePolicy(String value) {
        switch (value) {
            case "local":
                return WindowManager.DISPLAY_IME_POLICY_LOCAL;
            case "fallback":
                return WindowManager.DISPLAY_IME_POLICY_FALLBACK_DISPLAY;
            case "hide":
                return WindowManager.DISPLAY_IME_POLICY_HIDE;
            default:
                throw new IllegalArgumentException("Invalid display IME policy: " + value);
        }
    }

    /**
     * Apply client configuration from capability negotiation.
     *
     * This updates the options based on client's selected configuration.
     *
     * @param clientConfig Client configuration received from client
     */
    public void applyClientConfig(CapabilityNegotiation.ClientConfig clientConfig) {
        // Apply video codec
        this.videoCodec = clientConfig.getVideoCodec();
        Ln.i("Applied client video codec: " + this.videoCodec.getName());

        // Apply audio codec
        this.audioCodec = clientConfig.getAudioCodec();
        Ln.i("Applied client audio codec: " + this.audioCodec.getName());

        // Apply bitrates
        this.videoBitRate = clientConfig.videoBitrate;
        this.audioBitRate = clientConfig.audioBitrate;
        Ln.i("Applied client bitrates: video=" + this.videoBitRate + ", audio=" + this.audioBitRate);

        // Apply max fps
        this.maxFps = clientConfig.maxFps;
        Ln.i("Applied client max fps: " + this.maxFps);

        // Apply I-frame interval
        this.iFrameInterval = clientConfig.iFrameInterval;
        Ln.i("Applied client I-frame interval: " + this.iFrameInterval);

        // Apply flags
        this.video = clientConfig.isVideoEnabled();
        this.audio = clientConfig.isAudioEnabled();
        this.videoFecEnabled = clientConfig.isVideoFecEnabled();
        this.audioFecEnabled = clientConfig.isAudioFecEnabled();

        // Apply CBR mode
        if (clientConfig.isCbrMode()) {
            this.bitrateMode = "cbr";
        } else {
            this.bitrateMode = "vbr";
        }

        Ln.i("Applied client flags: video=" + this.video + ", audio=" + this.audio
             + ", videoFec=" + this.videoFecEnabled + ", audioFec=" + this.audioFecEnabled
             + ", cbr=" + clientConfig.isCbrMode());
    }
}
