package com.genymobile.scrcpy.companion;

import android.util.Log;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;

/**
 * UDP client for communicating with scrcpy server.
 */
public class UdpClient {

    private static final String TAG = "ScrcpyUdp";
    private static final int DISCOVERY_PORT = 27183;
    private static final String DISCOVER_REQUEST = "SCRCPY_DISCOVER";
    private static final String DISCOVER_RESPONSE_PREFIX = "SCRCPY_HERE";
    private static final String TERMINATE_REQUEST = "SCRCPY_TERMINATE";
    private static final String TERMINATE_RESPONSE = "SCRCPY_TERMINATE_ACK";

    /**
     * Discover server and return response string.
     * @return Response string like "SCRCPY_HERE DeviceName 192.168.x.x" or null if not found
     */
    public static String discoverServer() {
        return discoverServer("127.0.0.1");
    }

    /**
     * Discover server and return response string.
     * @param host Server host address
     * @return Response string or null if not found
     */
    public static String discoverServer(String host) {
        DatagramSocket socket = null;
        try {
            socket = new DatagramSocket();
            socket.setSoTimeout(1000);

            InetAddress address = InetAddress.getByName(host);
            byte[] data = DISCOVER_REQUEST.getBytes();
            DatagramPacket packet = new DatagramPacket(data, data.length, address, DISCOVERY_PORT);
            socket.send(packet);
            Log.d(TAG, "Sent discover request to " + host + ":" + DISCOVERY_PORT);

            byte[] buffer = new byte[256];
            DatagramPacket response = new DatagramPacket(buffer, buffer.length);
            socket.receive(response);
            String responseStr = new String(response.getData(), 0, response.getLength()).trim();

            if (responseStr.startsWith(DISCOVER_RESPONSE_PREFIX)) {
                Log.d(TAG, "Discovery response: " + responseStr);
                return responseStr;
            }
            return null;
        } catch (Exception e) {
            Log.d(TAG, "Discovery failed: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            return null;
        } finally {
            if (socket != null) {
                socket.close();
            }
        }
    }

    /**
     * Check if server is running by sending discovery request.
     * @return true if server responds
     */
    public static boolean isServerRunning() {
        return isServerRunning("127.0.0.1");
    }

    /**
     * Check if server is running by sending discovery request.
     * @param host Server host address
     * @return true if server responds
     */
    public static boolean isServerRunning(String host) {
        DatagramSocket socket = null;
        try {
            socket = new DatagramSocket();
            socket.setSoTimeout(1000);

            InetAddress address = InetAddress.getByName(host);
            byte[] data = DISCOVER_REQUEST.getBytes();
            DatagramPacket packet = new DatagramPacket(data, data.length, address, DISCOVERY_PORT);
            socket.send(packet);
            Log.d(TAG, "Sent discover request to " + host + ":" + DISCOVERY_PORT);

            byte[] buffer = new byte[256];
            DatagramPacket response = new DatagramPacket(buffer, buffer.length);
            socket.receive(response);
            String responseStr = new String(response.getData(), 0, response.getLength()).trim();

            boolean running = responseStr.startsWith(DISCOVER_RESPONSE_PREFIX);
            Log.d(TAG, "Discovery response: " + responseStr + ", running=" + running);
            return running;
        } catch (Exception e) {
            Log.e(TAG, "Discovery failed: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            return false;
        } finally {
            if (socket != null) {
                socket.close();
            }
        }
    }

    /**
     * Parse server mode from discovery response.
     * @param response Discovery response string like "SCRCPY_HERE DeviceName 192.168.x.x single"
     * @return Mode string ("single" or "stay-alive") or "unknown"
     */
    public static String parseMode(String response) {
        if (response == null || !response.startsWith(DISCOVER_RESPONSE_PREFIX)) {
            return "unknown";
        }
        String[] parts = response.split(" ");
        if (parts.length >= 4) {
            return parts[3]; // mode is the 4th part
        }
        return "unknown";
    }

    /**
     * Parse device name from discovery response.
     * @param response Discovery response string
     * @return Device name or "Unknown"
     */
    public static String parseDeviceName(String response) {
        if (response == null || !response.startsWith(DISCOVER_RESPONSE_PREFIX)) {
            return "Unknown";
        }
        String[] parts = response.split(" ");
        if (parts.length >= 2) {
            return parts[1];
        }
        return "Unknown";
    }

    /**
     * Parse IP address from discovery response.
     * @param response Discovery response string
     * @return IP address or "--"
     */
    public static String parseIp(String response) {
        if (response == null || !response.startsWith(DISCOVER_RESPONSE_PREFIX)) {
            return "--";
        }
        String[] parts = response.split(" ");
        if (parts.length >= 3) {
            return parts[2];
        }
        return "--";
    }

    /**
     * Send terminate request to server.
     * @return true if server acknowledged
     */
    public static boolean sendTerminateRequest() {
        return sendTerminateRequest("127.0.0.1");
    }

    /**
     * Send terminate request to server.
     * @param host Server host address
     * @return true if server acknowledged
     */
    public static boolean sendTerminateRequest(String host) {
        try {
            DatagramSocket socket = new DatagramSocket();
            socket.setSoTimeout(2000);

            InetAddress address = InetAddress.getByName(host);
            byte[] data = TERMINATE_REQUEST.getBytes();
            DatagramPacket packet = new DatagramPacket(data, data.length, address, DISCOVERY_PORT);
            socket.send(packet);
            Log.i(TAG, "Sent terminate request to " + host + ":" + DISCOVERY_PORT);

            byte[] buffer = new byte[256];
            DatagramPacket response = new DatagramPacket(buffer, buffer.length);
            socket.receive(response);
            String responseStr = new String(response.getData(), 0, response.getLength()).trim();
            socket.close();

            boolean acknowledged = TERMINATE_RESPONSE.equals(responseStr);
            Log.i(TAG, "Response: " + responseStr + ", acknowledged=" + acknowledged);
            return acknowledged;
        } catch (Exception e) {
            Log.e(TAG, "Failed to send terminate: " + e.getMessage());
            return false;
        }
    }
}
