package com.genymobile.scrcpy;

import com.genymobile.scrcpy.audio.AudioCapture;
import com.genymobile.scrcpy.audio.AudioCodec;
import com.genymobile.scrcpy.audio.AudioDirectCapture;
import com.genymobile.scrcpy.audio.AudioEncoder;
import com.genymobile.scrcpy.audio.AudioPlaybackCapture;
import com.genymobile.scrcpy.audio.AudioRawRecorder;
import com.genymobile.scrcpy.audio.AudioSource;
import com.genymobile.scrcpy.control.ControlChannel;
import com.genymobile.scrcpy.control.Controller;
import com.genymobile.scrcpy.device.CapabilityNegotiation;
import com.genymobile.scrcpy.device.ConfigurationException;
import com.genymobile.scrcpy.device.DesktopConnection;
import com.genymobile.scrcpy.device.Device;
import com.genymobile.scrcpy.device.DisplayInfo;
import com.genymobile.scrcpy.device.NewDisplay;
import com.genymobile.scrcpy.device.Size;
import com.genymobile.scrcpy.device.Streamer;
import com.genymobile.scrcpy.wrappers.ServiceManager;
import com.genymobile.scrcpy.opengl.OpenGLRunner;
import com.genymobile.scrcpy.udp.UdpDiscoveryReceiver;
import com.genymobile.scrcpy.util.Ln;
import com.genymobile.scrcpy.util.LogUtils;
import com.genymobile.scrcpy.video.CameraCapture;
import com.genymobile.scrcpy.video.NewDisplayCapture;
import com.genymobile.scrcpy.video.ScreenCapture;
import com.genymobile.scrcpy.video.ScreenshotCapture;
import com.genymobile.scrcpy.video.SurfaceCapture;
import com.genymobile.scrcpy.video.SurfaceEncoder;
import com.genymobile.scrcpy.video.VideoSource;

import android.annotation.SuppressLint;
import android.os.Build;
import android.os.Looper;

import java.io.File;
import java.io.IOException;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;

public final class Server {

    public static final String SERVER_PATH;

    static {
        String[] classPaths = System.getProperty("java.class.path").split(File.pathSeparator);
        // By convention, scrcpy is always executed with the absolute path of scrcpy-server.jar as the first item in the classpath
        SERVER_PATH = classPaths[0];
    }

    private static class Completion {
        private int running;
        private boolean fatalError;

        Completion(int running) {
            this.running = running;
        }

        synchronized void addCompleted(boolean fatalError) {
            --running;
            if (fatalError) {
                this.fatalError = true;
            }
            if (running == 0 || this.fatalError) {
                Looper.getMainLooper().quitSafely();
            }
        }
    }

    private Server() {
        // not instantiable
    }

    /**
     * Run single scrcpy session (traditional mode).
     * Also starts UDP discovery listener for scrcpy-companion query/terminate support.
     */
    private static void scrcpy(Options options) throws IOException, ConfigurationException {
        validateOptions(options);

        CleanUp cleanUp = null;
        if (options.getCleanup()) {
            cleanUp = CleanUp.start(options);
        }

        Workarounds.apply();

        // Start UDP discovery listener for scrcpy-companion query/terminate support
        UdpDiscoveryReceiver discovery = new UdpDiscoveryReceiver(options.getDiscoveryPort(), false);
        Thread terminateThread = new Thread(() -> discovery.listenForTerminate());
        terminateThread.setDaemon(true);
        terminateThread.start();
        Ln.i("UDP discovery listener started on port " + options.getDiscoveryPort() + " for query/terminate");

        DesktopConnection connection = createConnection(options);

        // Create a volatile reference for the monitor thread
        final DesktopConnection[] connectionRef = {connection};

        // Start a monitor thread to check for terminate requests
        Thread monitorThread = new Thread(() -> {
            while (!discovery.isTerminateRequested()) {
                try {
                    Thread.sleep(500);
                } catch (InterruptedException e) {
                    break;
                }
            }
            if (discovery.isTerminateRequested()) {
                Ln.i("Terminating session due to UDP terminate request");
                // Close connection to force scrcpySession to exit
                if (connectionRef[0] != null) {
                    try {
                        connectionRef[0].close();
                    } catch (IOException e) {
                        // Ignore
                    }
                }
            }
        });
        monitorThread.setDaemon(true);
        monitorThread.start();

        try {
            scrcpySession(options, connection, cleanUp);
        } finally {
            discovery.stop();
            monitorThread.interrupt();
            connection.close();
            Ln.i("Server session ended");
        }
    }

    /**
     * Run stay-alive mode (hot-connection loop).
     * Server keeps running and accepts multiple connections.
     */
    private static void runStayAliveMode(Options options) throws IOException, ConfigurationException {
        validateOptions(options);

        // Prepare Looper first - needed for Workarounds which creates a Handler
        prepareMainLooper();

        // CleanUp runs for entire lifetime (not per-connection)
        CleanUp cleanUp = null;
        if (options.getCleanup()) {
            cleanUp = CleanUp.start(options);
        }

        // Workarounds only need to be applied once
        Workarounds.apply();

        UdpDiscoveryReceiver discovery = new UdpDiscoveryReceiver(options.getDiscoveryPort(), true);
        int connectionCount = 0;
        int maxConnections = options.getMaxConnections();

        // Load auth key once at startup (for stay-alive mode, key is reused)
        byte[] authKey = null;
        if (options.getAuthKeyFile() != null) {
            File keyFile = new File(options.getAuthKeyFile());
            if (keyFile.exists()) {
                authKey = java.nio.file.Files.readAllBytes(keyFile.toPath());
                Ln.i("Auth key loaded from " + options.getAuthKeyFile() + " (" + authKey.length + " bytes)");
                // Delete key file after reading (security measure)
                if (keyFile.delete()) {
                    Ln.d("Auth key file deleted after reading");
                }
            } else {
                Ln.w("Auth key file not found: " + options.getAuthKeyFile());
            }
        }

        Ln.i("Stay-alive mode enabled. Listening on UDP port " + options.getDiscoveryPort());

        try {
            while (maxConnections < 0 || connectionCount < maxConnections) {
                if (discovery.isTerminateRequested()) {
                    Ln.i("Terminate requested, exiting server");
                    break;
                }

                Ln.i("Waiting for wake request... (connection #" + (connectionCount + 1) + ")");

                // Wait for wake request
                discovery.startListening();

                if (!discovery.isWakeRequested()) {
                    // Interrupted without wake request
                    Ln.i("Discovery interrupted, exiting stay-alive mode");
                    break;
                }

                if (discovery.isTerminateRequested()) {
                    Ln.i("Terminate request received, exiting server");
                    break;
                }

                connectionCount++;
                Ln.i("Wake request received, starting connection #" + connectionCount);

                // Start terminate listener for this session in background
                Thread terminateThread = new Thread(() -> discovery.listenForTerminate());
                terminateThread.setDaemon(true);
                terminateThread.start();

                // Create connection
                DesktopConnection connection = null;
                try {
                    connection = DesktopConnection.openNetwork(
                            options.getControlPort(),
                            options.getVideoPort(),
                            options.getAudioPort(),
                            options.getFilePort(),
                            options.getVideo(),
                            options.getAudio(),
                            options.getControl(),
                            true,  // file always enabled
                            options.getSendDummyByte(),
                            authKey  // auth key (null if not configured)
                    );

                    // Start a monitor thread to force close connection on terminate request
                    final DesktopConnection finalConnection = connection;
                    final UdpDiscoveryReceiver finalDiscovery = discovery;
                    Thread monitorThread = new Thread(() -> {
                        while (!finalDiscovery.isTerminateRequested()) {
                            try {
                                Thread.sleep(500);
                            } catch (InterruptedException e) {
                                break;
                            }
                        }
                        if (finalDiscovery.isTerminateRequested()) {
                            Ln.i("Terminating session due to UDP terminate request");
                            if (finalConnection != null) {
                                try {
                                    finalConnection.close();
                                } catch (IOException e) {
                                    // Ignore
                                }
                            }
                        }
                    });
                    monitorThread.setDaemon(true);
                    monitorThread.start();

                    // Run session
                    scrcpySession(options, connection, cleanUp);

                } catch (IOException e) {
                    Ln.w("Connection error: " + e.getMessage());
                } finally {
                    // Stop terminate listener
                    discovery.stop();
                    try {
                        terminateThread.join(1000);
                    } catch (InterruptedException e) {
                        // Ignore
                    }

                    if (connection != null) {
                        connection.close();
                    }
                }

                // Check terminate AFTER session ends but BEFORE reset
                if (discovery.isTerminateRequested()) {
                    Ln.i("Terminate requested during session, exiting server");
                    discovery.reset();
                    break;
                }

                // Reset discovery for next cycle (this clears terminateRequested!)
                discovery.reset();
                // Reset Looper so it can be created fresh for next session
                resetMainLooper();
                // Re-prepare Looper for next session
                prepareMainLooper();
                Ln.i("Session ended, returning to wait mode");
            }
        } finally {
            discovery.stop();
            Ln.i("Stay-alive mode ended after " + connectionCount + " connections");
        }
    }

    /**
     * Run a single scrcpy session with given connection.
     * Used by both single mode and stay-alive mode.
     */
    private static void scrcpySession(Options options, DesktopConnection connection, CleanUp cleanUp)
            throws IOException, ConfigurationException {

        int scid = options.getScid();
        boolean control = options.getControl();
        boolean video = options.getVideo();
        boolean audio = options.getAudio();
        boolean networkMode = options.isNetworkMode();

        List<AsyncProcessor> asyncProcessors = new ArrayList<>();

        try {
            if (options.getSendDeviceMeta()) {
                connection.sendDeviceMeta(Device.getDeviceName());
            }

            // Capability negotiation for network mode
            if (networkMode && options.getSendDeviceMeta()) {
                int displayId = options.getDisplayId();
                if (displayId == Device.DISPLAY_ID_NONE) {
                    displayId = 0;
                }
                DisplayInfo displayInfo = ServiceManager.getDisplayManager().getDisplayInfo(displayId);
                Size screenSize = displayInfo.getSize();
                connection.sendCapabilities(screenSize.getWidth(), screenSize.getHeight());

                CapabilityNegotiation.ClientConfig clientConfig = connection.receiveClientConfig();
                if (clientConfig != null) {
                    options.applyClientConfig(clientConfig);
                    video = options.getVideo();
                    audio = options.getAudio();
                }
            }

            Controller controller = null;

            if (control) {
                ControlChannel controlChannel = connection.getControlChannel();
                controller = new Controller(controlChannel, cleanUp, options);
                asyncProcessors.add(controller);
            }

            if (audio) {
                AudioCodec audioCodec = options.getAudioCodec();
                AudioSource audioSource = options.getAudioSource();
                AudioCapture audioCapture;
                if (audioSource.isDirect()) {
                    audioCapture = new AudioDirectCapture(audioSource);
                } else {
                    audioCapture = new AudioPlaybackCapture(options.getAudioDup());
                }

                Streamer audioStreamer;
                if (networkMode) {
                    com.genymobile.scrcpy.udp.UdpMediaSender audioUdpSender = connection.getAudioUdpSender();
                    if (options.isAudioFecEnabled() && audioUdpSender != null) {
                        audioUdpSender.enableFec(options.getFecGroupSize(), options.getFecParityCount(), options.getFecMode());
                    }
                    audioStreamer = new Streamer(audioUdpSender, audioCodec, options.getSendCodecMeta(), options.getSendFrameMeta());
                } else {
                    audioStreamer = new Streamer(connection.getAudioFd(), audioCodec, options.getSendCodecMeta(), options.getSendFrameMeta());
                }
                AsyncProcessor audioRecorder;
                if (audioCodec == AudioCodec.RAW) {
                    audioRecorder = new AudioRawRecorder(audioCapture, audioStreamer);
                } else {
                    AudioEncoder audioEncoder = new AudioEncoder(audioCapture, audioStreamer, options);
                    audioRecorder = audioEncoder;
                    if (controller != null) {
                        controller.setAudioEncoder(audioEncoder);
                    }
                }
                asyncProcessors.add(audioRecorder);
            }

            if (video) {
                Streamer videoStreamer;
                if (networkMode) {
                    com.genymobile.scrcpy.udp.UdpMediaSender videoUdpSender = connection.getVideoUdpSender();
                    if (options.isVideoFecEnabled() && videoUdpSender != null) {
                        videoUdpSender.enableFec(options.getFecGroupSize(), options.getFecParityCount(), options.getFecMode());
                    }
                    videoStreamer = new Streamer(videoUdpSender, options.getVideoCodec(), options.getSendCodecMeta(), options.getSendFrameMeta());
                } else {
                    videoStreamer = new Streamer(connection.getVideoFd(), options.getVideoCodec(), options.getSendCodecMeta(), options.getSendFrameMeta());
                }
                SurfaceCapture surfaceCapture;
                if (options.getVideoSource() == VideoSource.DISPLAY) {
                    NewDisplay newDisplay = options.getNewDisplay();
                    if (newDisplay != null) {
                        surfaceCapture = new NewDisplayCapture(controller, options);
                    } else {
                        assert options.getDisplayId() != Device.DISPLAY_ID_NONE;
                        surfaceCapture = new ScreenCapture(controller, options);
                    }
                } else {
                    surfaceCapture = new CameraCapture(options);
                }
                SurfaceEncoder surfaceEncoder = new SurfaceEncoder(surfaceCapture, videoStreamer, options);
                asyncProcessors.add(surfaceEncoder);

                if (controller != null) {
                    controller.setSurfaceCapture(surfaceCapture);
                    controller.setSurfaceEncoder(surfaceEncoder);
                    controller.setCaptureReset(surfaceEncoder.getCaptureReset());
                }
            } else if (networkMode && control) {
                // In network mode with video=false, create ScreenshotCapture for screenshot support
                // This creates a VirtualDisplay without encoding, just for capturing screenshots
                try {
                    ScreenshotCapture screenshotCapture = new ScreenshotCapture(options);
                    screenshotCapture.init();
                    Ln.i("ScreenshotCapture initialized for screenshot support (video=false mode)");

                    if (controller != null) {
                        controller.setScreenshotCapture(screenshotCapture);
                    }
                } catch (Exception e) {
                    Ln.e("Failed to initialize ScreenshotCapture: " + e.getMessage());
                    // Continue without screenshot support
                }
            }

            Completion completion = new Completion(asyncProcessors.size());
            for (AsyncProcessor asyncProcessor : asyncProcessors) {
                asyncProcessor.start((fatalError) -> {
                    completion.addCompleted(fatalError);
                });
            }

            Looper.loop();

        } finally {
            if (cleanUp != null) {
                cleanUp.interrupt();
            }
            for (AsyncProcessor asyncProcessor : asyncProcessors) {
                asyncProcessor.stop();
            }

            OpenGLRunner.quit();

            connection.shutdown();

            try {
                if (cleanUp != null) {
                    cleanUp.join();
                }
                for (AsyncProcessor asyncProcessor : asyncProcessors) {
                    asyncProcessor.join();
                }
                OpenGLRunner.join();
            } catch (InterruptedException e) {
                // ignore
            }
        }
    }

    /**
     * Validate options and throw ConfigurationException if invalid.
     */
    private static void validateOptions(Options options) throws ConfigurationException {
        if (Build.VERSION.SDK_INT < AndroidVersions.API_31_ANDROID_12 && options.getVideoSource() == VideoSource.CAMERA) {
            Ln.e("Camera mirroring is not supported before Android 12");
            throw new ConfigurationException("Camera mirroring is not supported");
        }

        if (Build.VERSION.SDK_INT < AndroidVersions.API_29_ANDROID_10) {
            if (options.getNewDisplay() != null) {
                Ln.e("New virtual display is not supported before Android 10");
                throw new ConfigurationException("New virtual display is not supported");
            }
            if (options.getDisplayImePolicy() != -1) {
                Ln.e("Display IME policy is not supported before Android 10");
                throw new ConfigurationException("Display IME policy is not supported");
            }
        }

        // Stay-alive mode requires network mode
        if (options.isStayAlive() && !options.isNetworkMode()) {
            Ln.e("Stay-alive mode requires network mode");
            throw new ConfigurationException("Stay-alive mode requires network mode (control_port > 0)");
        }
    }

    /**
     * Create connection based on mode.
     */
    private static DesktopConnection createConnection(Options options) throws IOException {
        int scid = options.getScid();
        boolean tunnelForward = options.isTunnelForward();
        boolean control = options.getControl();
        boolean video = options.getVideo();
        boolean audio = options.getAudio();
        boolean sendDummyByte = options.getSendDummyByte();
        boolean networkMode = options.isNetworkMode();
        boolean file = true;  // File transfer is always enabled

        // Load auth key if specified (network mode only)
        byte[] authKey = null;
        if (networkMode && options.getAuthKeyFile() != null) {
            File keyFile = new File(options.getAuthKeyFile());
            if (keyFile.exists()) {
                authKey = java.nio.file.Files.readAllBytes(keyFile.toPath());
                Ln.i("Auth key loaded from " + options.getAuthKeyFile() + " (" + authKey.length + " bytes)");
                // Delete key file after reading (single-use)
                if (keyFile.delete()) {
                    Ln.d("Auth key file deleted after reading");
                }
            } else {
                Ln.w("Auth key file not found: " + options.getAuthKeyFile());
            }
        }

        if (networkMode) {
            Ln.i("Starting in network mode (TCP direct connection)");
            return DesktopConnection.openNetwork(
                    options.getControlPort(),
                    options.getVideoPort(),
                    options.getAudioPort(),
                    options.getFilePort(),
                    video,
                    audio,
                    control,
                    file,
                    sendDummyByte,
                    authKey
            );
        } else {
            return DesktopConnection.open(scid, tunnelForward, video, audio, control, file, sendDummyByte);
        }
    }

    private static void prepareMainLooper() {
        // Like Looper.prepareMainLooper(), but with quitAllowed set to true
        Looper.prepare();
        synchronized (Looper.class) {
            try {
                @SuppressLint("DiscouragedPrivateApi")
                Field field = Looper.class.getDeclaredField("sMainLooper");
                field.setAccessible(true);
                field.set(null, Looper.myLooper());
            } catch (ReflectiveOperationException e) {
                throw new AssertionError(e);
            }
        }
    }

    /**
     * Reset the main Looper to allow creating a new one.
     * This is needed for stay-alive mode where we need a fresh Looper for each session.
     */
    @SuppressLint("DiscouragedPrivateApi")
    private static void resetMainLooper() {
        try {
            // Clear sMainLooper static field
            Field mainLooperField = Looper.class.getDeclaredField("sMainLooper");
            mainLooperField.setAccessible(true);
            mainLooperField.set(null, null);

            // Clear thread-local Looper (sThreadLocal)
            Field threadLocalField = Looper.class.getDeclaredField("sThreadLocal");
            threadLocalField.setAccessible(true);
            @SuppressWarnings("unchecked")
            ThreadLocal<Looper> threadLocal = (ThreadLocal<Looper>) threadLocalField.get(null);
            if (threadLocal != null) {
                threadLocal.remove();
            }
        } catch (ReflectiveOperationException e) {
            Ln.w("Failed to reset main looper: " + e.getMessage());
        }
    }

    public static void main(String... args) {
        int status = 0;
        try {
            internalMain(args);
        } catch (Throwable t) {
            Ln.e(t.getMessage(), t);
            status = 1;
        } finally {
            // By default, the Java process exits when all non-daemon threads are terminated.
            // The Android SDK might start some non-daemon threads internally, preventing the scrcpy server to exit.
            // So force the process to exit explicitly.
            System.exit(status);
        }
    }

    private static void internalMain(String... args) throws Exception {
        Thread.UncaughtExceptionHandler defaultHandler = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
            Ln.e("Exception on thread " + t, e);
            if (defaultHandler != null) {
                defaultHandler.uncaughtException(t, e);
            }
        });

        Options options = Options.parse(args);

        Ln.disableSystemStreams();
        Ln.initLogLevel(options.getLogLevel());

        Ln.i("Device: [" + Build.MANUFACTURER + "] " + Build.BRAND + " " + Build.MODEL + " (Android " + Build.VERSION.RELEASE + ")");

        if (options.getList()) {
            if (options.getCleanup()) {
                CleanUp.unlinkSelf();
            }

            if (options.getListEncoders()) {
                Ln.i(LogUtils.buildVideoEncoderListMessage());
                Ln.i(LogUtils.buildAudioEncoderListMessage());
            }
            if (options.getListDisplays()) {
                Ln.i(LogUtils.buildDisplayListMessage());
            }
            if (options.getListCameras() || options.getListCameraSizes()) {
                Workarounds.apply();
                Ln.i(LogUtils.buildCameraListMessage(options.getListCameraSizes()));
            }
            if (options.getListApps()) {
                Workarounds.apply();
                Ln.i("Processing Android apps... (this may take some time)");
                Ln.i(LogUtils.buildAppListMessage());
            }
            // Just print the requested data, do not mirror
            return;
        }

        try {
            if (options.isStayAlive()) {
                // Hot-connection mode: persistent server with UDP wake
                // Looper will be created/reset for each session in runStayAliveMode
                runStayAliveMode(options);
            } else {
                // Traditional mode: single connection
                prepareMainLooper();
                scrcpy(options);
            }
        } catch (ConfigurationException e) {
            // Do not print stack trace, a user-friendly error-message has already been logged
        }
    }
}
