package com.genymobile.scrcpy.device;

import com.genymobile.scrcpy.control.ControlChannel;
import com.genymobile.scrcpy.device.CapabilityNegotiation;
import com.genymobile.scrcpy.udp.UdpMediaSender;
import com.genymobile.scrcpy.util.IO;
import com.genymobile.scrcpy.util.Ln;
import com.genymobile.scrcpy.util.StringUtils;

import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import java.io.Closeable;
import java.io.FileDescriptor;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

public final class DesktopConnection implements Closeable {

    private static final int DEVICE_NAME_FIELD_LENGTH = 64;

    private static final String SOCKET_NAME_PREFIX = "scrcpy";

    // Network mode fields
    private final boolean networkMode;
    private Socket controlTcpSocket;
    private OutputStream videoOutputStream;
    private OutputStream audioOutputStream;
    private InputStream controlInputStream;

    // UDP media senders for network mode
    private UdpMediaSender videoUdpSender;
    private UdpMediaSender audioUdpSender;
    private DatagramSocket videoUdpSocket;
    private DatagramSocket audioUdpSocket;
    private InetAddress clientAddress;
    private int clientVideoPort;
    private int clientAudioPort;

    private final LocalSocket videoSocket;
    private final FileDescriptor videoFd;

    private final LocalSocket audioSocket;
    private final FileDescriptor audioFd;

    private final LocalSocket controlSocket;
    private final ControlChannel controlChannel;

    private DesktopConnection(LocalSocket videoSocket, LocalSocket audioSocket, LocalSocket controlSocket) throws IOException {
        this.networkMode = false;
        this.videoSocket = videoSocket;
        this.audioSocket = audioSocket;
        this.controlSocket = controlSocket;

        videoFd = videoSocket != null ? videoSocket.getFileDescriptor() : null;
        audioFd = audioSocket != null ? audioSocket.getFileDescriptor() : null;
        controlChannel = controlSocket != null ? new ControlChannel(controlSocket) : null;

        this.controlTcpSocket = null;
        this.videoOutputStream = null;
        this.audioOutputStream = null;
        this.controlInputStream = null;
        this.videoUdpSender = null;
        this.audioUdpSender = null;
        this.videoUdpSocket = null;
        this.audioUdpSocket = null;
        this.clientAddress = null;
        this.clientVideoPort = 0;
        this.clientAudioPort = 0;
    }

    // Network mode constructor (TCP control + UDP media)
    private DesktopConnection(Socket controlTcpSocket,
                              InetAddress clientAddress, int clientVideoPort, int clientAudioPort,
                              boolean video, boolean audio, boolean control) throws IOException {
        this.networkMode = true;
        this.videoSocket = null;
        this.audioSocket = null;
        this.controlSocket = null;
        this.videoFd = null;
        this.audioFd = null;

        this.controlTcpSocket = controlTcpSocket;
        this.clientAddress = clientAddress;
        this.clientVideoPort = clientVideoPort;
        this.clientAudioPort = clientAudioPort;

        // Create UDP sockets and senders for video/audio
        if (video && clientVideoPort > 0) {
            this.videoUdpSocket = new DatagramSocket();
            this.videoUdpSender = new UdpMediaSender(videoUdpSocket, clientAddress, clientVideoPort);
            this.videoOutputStream = null; // Not using TCP for video
            Ln.i("Video UDP sender created: " + clientAddress + ":" + clientVideoPort);
        } else {
            this.videoUdpSocket = null;
            this.videoUdpSender = null;
            this.videoOutputStream = null;
        }

        if (audio && clientAudioPort > 0) {
            this.audioUdpSocket = new DatagramSocket();
            this.audioUdpSender = new UdpMediaSender(audioUdpSocket, clientAddress, clientAudioPort);
            this.audioOutputStream = null; // Not using TCP for audio
            Ln.i("Audio UDP sender created: " + clientAddress + ":" + clientAudioPort);
        } else {
            this.audioUdpSocket = null;
            this.audioUdpSender = null;
            this.audioOutputStream = null;
        }

        if (control && controlTcpSocket != null) {
            this.controlInputStream = controlTcpSocket.getInputStream();
            this.controlChannel = new ControlChannel(controlTcpSocket);
        } else {
            this.controlInputStream = null;
            this.controlChannel = null;
        }
    }

    private static LocalSocket connect(String abstractName) throws IOException {
        LocalSocket localSocket = new LocalSocket();
        localSocket.connect(new LocalSocketAddress(abstractName));
        return localSocket;
    }

    private static String getSocketName(int scid) {
        if (scid == -1) {
            // If no SCID is set, use "scrcpy" to simplify using scrcpy-server alone
            return SOCKET_NAME_PREFIX;
        }

        return SOCKET_NAME_PREFIX + String.format("_%08x", scid);
    }

    public static DesktopConnection open(int scid, boolean tunnelForward, boolean video, boolean audio, boolean control, boolean sendDummyByte)
            throws IOException {
        String socketName = getSocketName(scid);

        LocalSocket videoSocket = null;
        LocalSocket audioSocket = null;
        LocalSocket controlSocket = null;
        try {
            if (tunnelForward) {
                try (LocalServerSocket localServerSocket = new LocalServerSocket(socketName)) {
                    if (video) {
                        videoSocket = localServerSocket.accept();
                        if (sendDummyByte) {
                            // send one byte so the client may read() to detect a connection error
                            videoSocket.getOutputStream().write(0);
                            sendDummyByte = false;
                        }
                    }
                    if (audio) {
                        audioSocket = localServerSocket.accept();
                        if (sendDummyByte) {
                            // send one byte so the client may read() to detect a connection error
                            audioSocket.getOutputStream().write(0);
                            sendDummyByte = false;
                        }
                    }
                    if (control) {
                        controlSocket = localServerSocket.accept();
                        if (sendDummyByte) {
                            // send one byte so the client may read() to detect a connection error
                            controlSocket.getOutputStream().write(0);
                            sendDummyByte = false;
                        }
                    }
                }
            } else {
                if (video) {
                    videoSocket = connect(socketName);
                }
                if (audio) {
                    audioSocket = connect(socketName);
                }
                if (control) {
                    controlSocket = connect(socketName);
                }
            }
        } catch (IOException | RuntimeException e) {
            if (videoSocket != null) {
                videoSocket.close();
            }
            if (audioSocket != null) {
                audioSocket.close();
            }
            if (controlSocket != null) {
                controlSocket.close();
            }
            throw e;
        }

        return new DesktopConnection(videoSocket, audioSocket, controlSocket);
    }

    // Network mode: TCP control + UDP media
    // Client connects to control port, then server sends video/audio via UDP
    public static DesktopConnection openNetwork(int controlPort, int videoPort, int audioPort,
                                                 boolean video, boolean audio, boolean control,
                                                 boolean sendDummyByte) throws IOException {
        Socket controlTcpSocket = null;
        ServerSocket controlServerSocket = null;

        try {
            // Only control uses TCP server socket
            if (control) {
                controlServerSocket = new ServerSocket(controlPort);
                Ln.i("Control TCP server listening on port " + controlPort);
            }

            // Accept control connection
            if (control) {
                controlTcpSocket = controlServerSocket.accept();
                controlServerSocket.close();
                InetAddress clientAddr = controlTcpSocket.getInetAddress();
                Ln.i("Control client connected from " + clientAddr);

                if (sendDummyByte) {
                    controlTcpSocket.getOutputStream().write(0);
                }

                // Now create UDP senders using client's IP and ports
                // videoPort and audioPort are the client's UDP listening ports
                return new DesktopConnection(controlTcpSocket, clientAddr, videoPort, audioPort, video, audio, control);
            } else {
                throw new IOException("Control connection is required for network mode");
            }

        } catch (IOException | RuntimeException e) {
            if (controlTcpSocket != null) {
                controlTcpSocket.close();
            }
            if (controlServerSocket != null && !controlServerSocket.isClosed()) {
                controlServerSocket.close();
            }
            throw e;
        }
    }

    private LocalSocket getFirstSocket() {
        if (videoSocket != null) {
            return videoSocket;
        }
        if (audioSocket != null) {
            return audioSocket;
        }
        return controlSocket;
    }

    public void shutdown() throws IOException {
        if (networkMode) {
            if (controlTcpSocket != null) {
                try {
                    controlTcpSocket.shutdownInput();
                } catch (IOException e) {
                    // Ignore - socket may already be closed
                }
                try {
                    controlTcpSocket.shutdownOutput();
                } catch (IOException e) {
                    // Ignore - socket may already be closed
                }
            }
            // UDP sockets don't need shutdown
        } else {
            if (videoSocket != null) {
                try {
                    videoSocket.shutdownInput();
                    videoSocket.shutdownOutput();
                } catch (IOException e) {
                    // Ignore
                }
            }
            if (audioSocket != null) {
                try {
                    audioSocket.shutdownInput();
                    audioSocket.shutdownOutput();
                } catch (IOException e) {
                    // Ignore
                }
            }
            if (controlSocket != null) {
                try {
                    controlSocket.shutdownInput();
                    controlSocket.shutdownOutput();
                } catch (IOException e) {
                    // Ignore
                }
            }
        }
    }

    public void close() throws IOException {
        if (networkMode) {
            if (controlTcpSocket != null) {
                controlTcpSocket.close();
            }
            if (videoUdpSender != null) {
                videoUdpSender.close();
            }
            if (audioUdpSender != null) {
                audioUdpSender.close();
            }
            if (videoUdpSocket != null && !videoUdpSocket.isClosed()) {
                videoUdpSocket.close();
            }
            if (audioUdpSocket != null && !audioUdpSocket.isClosed()) {
                audioUdpSocket.close();
            }
        } else {
            if (videoSocket != null) {
                videoSocket.close();
            }
            if (audioSocket != null) {
                audioSocket.close();
            }
            if (controlSocket != null) {
                controlSocket.close();
            }
        }
    }

    public void sendDeviceMeta(String deviceName) throws IOException {
        byte[] buffer = new byte[DEVICE_NAME_FIELD_LENGTH];

        byte[] deviceNameBytes = deviceName.getBytes(StandardCharsets.UTF_8);
        int len = StringUtils.getUtf8TruncationIndex(deviceNameBytes, DEVICE_NAME_FIELD_LENGTH - 1);
        System.arraycopy(deviceNameBytes, 0, buffer, 0, len);
        // byte[] are always 0-initialized in java, no need to set '\0' explicitly

        if (networkMode) {
            // In network mode, send via control TCP connection
            if (controlTcpSocket != null) {
                controlTcpSocket.getOutputStream().write(buffer);
                controlTcpSocket.getOutputStream().flush();
            }
        } else {
            FileDescriptor fd = getFirstSocket().getFileDescriptor();
            IO.writeFully(fd, buffer, 0, buffer.length);
        }
    }

    /**
     * Send device capabilities to client for capability negotiation.
     *
     * @param screenWidth Screen width
     * @param screenHeight Screen height
     * @throws IOException if sending fails
     */
    public void sendCapabilities(int screenWidth, int screenHeight) throws IOException {
        if (networkMode && controlTcpSocket != null) {
            Ln.i("Sending device capabilities to client...");
            CapabilityNegotiation.sendCapabilities(
                controlTcpSocket.getOutputStream(),
                screenWidth,
                screenHeight
            );
            Ln.i("Device capabilities sent");
        } else if (!networkMode) {
            // ADB mode: use first socket
            FileDescriptor fd = getFirstSocket().getFileDescriptor();
            // For ADB mode, we still use the old protocol without capability negotiation
            // This maintains backward compatibility
        }
    }

    /**
     * Receive client configuration for capability negotiation.
     *
     * @return Client configuration, or null if capability negotiation is not supported
     * @throws IOException if receiving fails
     */
    public CapabilityNegotiation.ClientConfig receiveClientConfig() throws IOException {
        if (networkMode && controlTcpSocket != null) {
            Ln.i("Waiting for client configuration...");

            // Read client config (32 bytes)
            byte[] buffer = new byte[32];
            InputStream input = controlTcpSocket.getInputStream();

            int totalRead = 0;
            while (totalRead < buffer.length) {
                int read = input.read(buffer, totalRead, buffer.length - totalRead);
                if (read < 0) {
                    throw new IOException("Connection closed while reading client config");
                }
                totalRead += read;
            }

            CapabilityNegotiation.ClientConfig config = CapabilityNegotiation.parseClientConfig(buffer);
            Ln.i("Received client configuration: video=" + config.getVideoCodec().getName()
                 + ", audio=" + config.getAudioCodec().getName()
                 + ", video_bitrate=" + config.videoBitrate
                 + ", cbr=" + config.isCbrMode());

            return config;
        }
        return null;
    }

    public FileDescriptor getVideoFd() {
        return videoFd;
    }

    public FileDescriptor getAudioFd() {
        return audioFd;
    }

    public OutputStream getVideoOutputStream() {
        return videoOutputStream;
    }

    public OutputStream getAudioOutputStream() {
        return audioOutputStream;
    }

    public UdpMediaSender getVideoUdpSender() {
        return videoUdpSender;
    }

    public UdpMediaSender getAudioUdpSender() {
        return audioUdpSender;
    }

    public boolean isNetworkMode() {
        return networkMode;
    }

    public ControlChannel getControlChannel() {
        return controlChannel;
    }
}
