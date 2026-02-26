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
import java.io.DataInputStream;
import java.io.DataOutputStream;
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
    private Socket fileTcpSocket;  // File transfer socket (network mode)
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
    private int clientFilePort;  // File transfer port

    private final LocalSocket videoSocket;
    private final FileDescriptor videoFd;

    private final LocalSocket audioSocket;
    private final FileDescriptor audioFd;

    private final LocalSocket controlSocket;
    private final ControlChannel controlChannel;

    // File socket for ADB tunnel mode
    private final LocalSocket fileSocket;
    private final FileDescriptor fileFd;

    private DesktopConnection(LocalSocket videoSocket, LocalSocket audioSocket, LocalSocket controlSocket, LocalSocket fileSocket) throws IOException {
        this.networkMode = false;
        this.videoSocket = videoSocket;
        this.audioSocket = audioSocket;
        this.controlSocket = controlSocket;
        this.fileSocket = fileSocket;

        videoFd = videoSocket != null ? videoSocket.getFileDescriptor() : null;
        audioFd = audioSocket != null ? audioSocket.getFileDescriptor() : null;
        fileFd = fileSocket != null ? fileSocket.getFileDescriptor() : null;
        controlChannel = controlSocket != null ? new ControlChannel(controlSocket) : null;

        this.controlTcpSocket = null;
        this.fileTcpSocket = null;
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
        this.clientFilePort = 0;
    }

    // Network mode constructor (TCP control + UDP media + TCP file)
    private DesktopConnection(Socket controlTcpSocket,
                              InetAddress clientAddress, int clientVideoPort, int clientAudioPort, int clientFilePort,
                              boolean video, boolean audio, boolean control, boolean file) throws IOException {
        this.networkMode = true;
        this.videoSocket = null;
        this.audioSocket = null;
        this.controlSocket = null;
        this.fileSocket = null;
        this.videoFd = null;
        this.audioFd = null;
        this.fileFd = null;

        this.controlTcpSocket = controlTcpSocket;
        this.clientAddress = clientAddress;
        this.clientVideoPort = clientVideoPort;
        this.clientAudioPort = clientAudioPort;
        this.clientFilePort = clientFilePort;

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

        // File socket will be connected later via connectFileSocket()
        this.fileTcpSocket = null;
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

    public static DesktopConnection open(int scid, boolean tunnelForward, boolean video, boolean audio, boolean control, boolean file, boolean sendDummyByte)
            throws IOException {
        String socketName = getSocketName(scid);

        LocalSocket videoSocket = null;
        LocalSocket audioSocket = null;
        LocalSocket controlSocket = null;
        LocalSocket fileSocket = null;
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
                    if (file) {
                        fileSocket = localServerSocket.accept();
                        Ln.i("File socket connected (forward mode)");
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
                // File socket uses same socket name (reverse mode connects to same tunnel)
                if (file) {
                    fileSocket = connect(socketName);
                    Ln.i("File socket connected (reverse mode)");
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
            if (fileSocket != null) {
                fileSocket.close();
            }
            throw e;
        }

        return new DesktopConnection(videoSocket, audioSocket, controlSocket, fileSocket);
    }

    // Network mode: TCP control + UDP media + TCP file
    // Client connects to control port, then server sends video/audio via UDP
    // File transfer uses separate TCP connection
    public static DesktopConnection openNetwork(int controlPort, int videoPort, int audioPort, int filePort,
                                                 boolean video, boolean audio, boolean control, boolean file,
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
                // filePort is the client's TCP listening port for file transfer
                DesktopConnection connection = new DesktopConnection(controlTcpSocket, clientAddr, videoPort, audioPort, filePort, video, audio, control, file);

                // Connect file socket immediately for network mode
                if (file && filePort > 0) {
                    connection.connectFileSocket();
                }

                return connection;
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

    /**
     * Connect to client's file socket (network mode only).
     * Called after connection is established, when file transfer is needed.
     * Starts a handler thread to process file requests.
     */
    public void connectFileSocket() throws IOException {
        if (!networkMode || fileTcpSocket != null) {
            return;
        }
        if (clientFilePort <= 0) {
            throw new IOException("File port not configured");
        }
        fileTcpSocket = new Socket(clientAddress, clientFilePort);
        Ln.i("File socket connected to " + clientAddress + ":" + clientFilePort);

        // Start file channel handler thread for network mode
        startFileChannelHandler();
    }

    /**
     * Start file channel handler thread (network mode).
     * This handles file requests from the client on the connected socket.
     */
    private void startFileChannelHandler() {
        if (fileTcpSocket == null) {
            return;
        }

        Thread handlerThread = new Thread(() -> {
            try {
                DataInputStream input = new DataInputStream(fileTcpSocket.getInputStream());
                DataOutputStream output = new DataOutputStream(fileTcpSocket.getOutputStream());

                Ln.i("File channel handler started (network mode)");

                while (!fileTcpSocket.isClosed()) {
                    try {
                        // Read frame header: [cmd:1B][length:4B]
                        int cmd = input.readUnsignedByte();
                        int length = input.readInt();

                        // Read payload
                        byte[] payload = null;
                        if (length > 0) {
                            payload = new byte[length];
                            input.readFully(payload);
                        } else {
                            payload = new byte[0];
                        }

                        // Handle command
                        com.genymobile.scrcpy.file.FileChannelHandler.handle(cmd, payload, output);
                        output.flush();

                    } catch (java.net.SocketException e) {
                        Ln.d("File channel handler: socket closed");
                        break;
                    } catch (IOException e) {
                        Ln.e("File channel handler error", e);
                        break;
                    }
                }
            } catch (IOException e) {
                Ln.e("Failed to start file channel handler", e);
            } finally {
                Ln.i("File channel handler stopped (network mode)");
            }
        }, "FileChannelHandler-Network");
        handlerThread.setDaemon(true);
        handlerThread.start();
    }

    /**
     * Get file socket input stream (network mode).
     */
    public java.io.InputStream getFileInputStream() throws IOException {
        if (networkMode) {
            if (fileTcpSocket != null) {
                return fileTcpSocket.getInputStream();
            }
            return null;
        } else {
            if (fileSocket != null) {
                return fileSocket.getInputStream();
            }
            return null;
        }
    }

    /**
     * Get file socket output stream (network mode).
     */
    public java.io.OutputStream getFileOutputStream() throws IOException {
        if (networkMode) {
            if (fileTcpSocket != null) {
                return fileTcpSocket.getOutputStream();
            }
            return null;
        } else {
            if (fileSocket != null) {
                return fileSocket.getOutputStream();
            }
            return null;
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
            if (fileTcpSocket != null) {
                try {
                    fileTcpSocket.shutdownInput();
                } catch (IOException e) {
                    // Ignore
                }
                try {
                    fileTcpSocket.shutdownOutput();
                } catch (IOException e) {
                    // Ignore
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
            if (fileSocket != null) {
                try {
                    fileSocket.shutdownInput();
                    fileSocket.shutdownOutput();
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
            if (fileTcpSocket != null) {
                fileTcpSocket.close();
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
            if (fileSocket != null) {
                fileSocket.close();
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
