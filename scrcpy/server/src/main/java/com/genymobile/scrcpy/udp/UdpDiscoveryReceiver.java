package com.genymobile.scrcpy.udp;

import com.genymobile.scrcpy.util.Ln;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketTimeoutException;
import java.util.Enumeration;

public class UdpDiscoveryReceiver {
    private static final int DISCOVERY_PORT = 27183;
    private static final int BUFFER_SIZE = 1024;
    private static final String DISCOVER_REQUEST = "SCRCPY_DISCOVER";
    private static final String DISCOVER_RESPONSE_PREFIX = "SCRCPY_HERE ";
    private static final String WAKE_REQUEST = "WAKE_UP";
    private static final String WAKE_RESPONSE = "WAKE_ACK";
    private static final String TERMINATE_REQUEST = "SCRCPY_TERMINATE";
    private static final String TERMINATE_RESPONSE = "SCRCPY_TERMINATE_ACK";

    private final int port;
    private final boolean stayAliveMode;
    private volatile boolean running = false;
    private DatagramSocket socket;
    private volatile boolean wakeRequested = false;
    private volatile boolean terminateRequested = false;
    private volatile InetAddress clientAddress;

    public UdpDiscoveryReceiver() {
        this(DISCOVERY_PORT, false);
    }

    public UdpDiscoveryReceiver(int port) {
        this(port, false);
    }

    public UdpDiscoveryReceiver(int port, boolean stayAliveMode) {
        this.port = port;
        this.stayAliveMode = stayAliveMode;
    }

    public void startListening() throws IOException {
        socket = new DatagramSocket(port);
        socket.setSoTimeout(1000);
        running = true;

        byte[] buffer = new byte[BUFFER_SIZE];
        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);

        Ln.i("UDP Discovery listening on port " + port);

        while (running && !wakeRequested && !terminateRequested) {
            try {
                socket.receive(packet);
                String message = new String(packet.getData(), 0, packet.getLength()).trim();
                InetAddress senderAddress = packet.getAddress();

                Ln.d("Received UDP message: " + message + " from " + senderAddress);

                if (DISCOVER_REQUEST.equals(message)) {
                    handleDiscover(senderAddress, packet.getPort());
                } else if (WAKE_REQUEST.equals(message)) {
                    handleWake(senderAddress, packet.getPort());
                } else if (TERMINATE_REQUEST.equals(message)) {
                    handleTerminate(senderAddress, packet.getPort());
                }
            } catch (SocketTimeoutException e) {
                // Continue loop to check running flag
            }
        }

        Ln.i("UDP Discovery stopped");
    }

    private void handleDiscover(InetAddress senderAddress, int senderPort) throws IOException {
        String deviceName = getDeviceName();
        String localIp = getLocalIpAddress();
        String mode = stayAliveMode ? "stay-alive" : "single";
        String response = DISCOVER_RESPONSE_PREFIX + deviceName + " " + localIp + " " + mode;

        byte[] data = response.getBytes();
        DatagramPacket responsePacket = new DatagramPacket(data, data.length, senderAddress, senderPort);
        socket.send(responsePacket);

        Ln.i("Sent discovery response to " + senderAddress + ":" + senderPort);
    }

    private void handleWake(InetAddress senderAddress, int senderPort) throws IOException {
        clientAddress = senderAddress;
        wakeRequested = true;

        byte[] data = WAKE_RESPONSE.getBytes();
        DatagramPacket responsePacket = new DatagramPacket(data, data.length, senderAddress, senderPort);
        socket.send(responsePacket);

        Ln.i("Wake request received from " + senderAddress + ", starting server...");
    }

    private void handleTerminate(InetAddress senderAddress, int senderPort) throws IOException {
        byte[] data = TERMINATE_RESPONSE.getBytes();
        DatagramPacket responsePacket = new DatagramPacket(data, data.length, senderAddress, senderPort);
        socket.send(responsePacket);

        terminateRequested = true;
        Ln.i("Terminate request received from " + senderAddress);
    }

    private String getDeviceName() {
        return android.os.Build.MODEL;
    }

    private String getLocalIpAddress() {
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                NetworkInterface iface = interfaces.nextElement();
                if (iface.isLoopback() || !iface.isUp()) continue;

                Enumeration<InetAddress> addresses = iface.getInetAddresses();
                while (addresses.hasMoreElements()) {
                    InetAddress addr = addresses.nextElement();
                    if (addr.isLoopbackAddress()) continue;

                    String ip = addr.getHostAddress();
                    // Prefer IPv4
                    if (ip != null && !ip.contains(":")) {
                        return ip;
                    }
                }
            }
        } catch (Exception e) {
            Ln.e("Failed to get local IP: " + e.getMessage());
        }
        return "0.0.0.0";
    }

    public void stop() {
        running = false;
        if (socket != null && !socket.isClosed()) {
            socket.close();
        }
    }

    /**
     * Stop listening and close socket.
     * Use this to completely stop the discovery receiver.
     */
    public void stopListening() {
        running = false;
        wakeRequested = true; // Also break out of the loop
        if (socket != null && !socket.isClosed()) {
            socket.close();
        }
    }

    /**
     * Reset state for reuse in next connection cycle.
     * Call this after a connection ends to prepare for the next wake.
     */
    public void reset() {
        wakeRequested = false;
        terminateRequested = false;
        clientAddress = null;
        // Close socket so it can be recreated
        if (socket != null && !socket.isClosed()) {
            socket.close();
        }
        socket = null;
    }

    /**
     * Listen for terminate command during session.
     * Also responds to discovery requests.
     * Should be called in a background thread.
     */
    public void listenForTerminate() {
        // Ensure running is set
        running = true;

        try {
            if (socket != null && !socket.isClosed()) {
                socket.close();
            }
            socket = new DatagramSocket(port);
            socket.setSoTimeout(1000);
        } catch (IOException e) {
            Ln.e("Failed to create socket for terminate listening: " + e.getMessage());
            running = false;
            return;
        }

        Ln.i("Terminate listener started on port " + port);

        byte[] buffer = new byte[BUFFER_SIZE];
        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);

        while (running && !terminateRequested) {
            try {
                socket.receive(packet);
                String message = new String(packet.getData(), 0, packet.getLength()).trim();
                InetAddress senderAddress = packet.getAddress();
                int senderPort = packet.getPort();

                if (TERMINATE_REQUEST.equals(message)) {
                    handleTerminate(senderAddress, senderPort);
                } else if (DISCOVER_REQUEST.equals(message)) {
                    // Also respond to discovery requests during session
                    handleDiscover(senderAddress, senderPort);
                }
            } catch (SocketTimeoutException e) {
                // Continue loop
            } catch (IOException e) {
                if (running) {
                    Ln.d("Terminate listener error: " + e.getMessage());
                }
            }
        }

        Ln.i("Terminate listener stopped");
    }

    public boolean isWakeRequested() {
        return wakeRequested;
    }

    public boolean isTerminateRequested() {
        return terminateRequested;
    }

    public InetAddress getClientAddress() {
        return clientAddress;
    }

    public int getPort() {
        return port;
    }
}
