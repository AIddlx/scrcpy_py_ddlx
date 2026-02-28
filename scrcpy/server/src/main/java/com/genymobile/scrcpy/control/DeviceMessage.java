package com.genymobile.scrcpy.control;

import java.util.List;
import com.genymobile.scrcpy.device.DeviceApp;

public final class DeviceMessage {

    public static final int TYPE_CLIPBOARD = 0;
    public static final int TYPE_ACK_CLIPBOARD = 1;
    public static final int TYPE_UHID_OUTPUT = 2;
    public static final int TYPE_APP_LIST = 3;
    public static final int TYPE_SCREENSHOT = 4;  // Screenshot image data (JPEG)
    public static final int TYPE_PONG = 5;        // Heartbeat response (server -> client)
    public static final int TYPE_FILE_CHANNEL_INFO = 6;  // File channel port info

    // Authentication message types (v1.4)
    public static final int TYPE_CHALLENGE = 0xF0;    // Server -> Client: authentication challenge
    public static final int TYPE_AUTH_RESULT = 0xF2;  // Server -> Client: authentication result

    private int type;
    private String text;
    private long sequence;
    private int id;
    private byte[] data;
    private List<DeviceApp> apps;
    private long timestamp;  // For PONG message
    private int port;        // For FILE_CHANNEL_INFO
    private int sessionId;   // For FILE_CHANNEL_INFO

    private DeviceMessage() {
    }

    public static DeviceMessage createClipboard(String text) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_CLIPBOARD;
        event.text = text;
        return event;
    }

    public static DeviceMessage createAckClipboard(long sequence) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_ACK_CLIPBOARD;
        event.sequence = sequence;
        return event;
    }

    public static DeviceMessage createUhidOutput(int id, byte[] data) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_UHID_OUTPUT;
        event.id = id;
        event.data = data;
        return event;
    }

    public static DeviceMessage createAppList(List<DeviceApp> apps) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_APP_LIST;
        event.apps = apps;
        return event;
    }

    public static DeviceMessage createScreenshot(byte[] data) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_SCREENSHOT;
        event.data = data;
        return event;
    }

    public static DeviceMessage createPong(long timestamp) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_PONG;
        event.timestamp = timestamp;
        return event;
    }

    public static DeviceMessage createFileChannelInfo(int port, int sessionId) {
        DeviceMessage event = new DeviceMessage();
        event.type = TYPE_FILE_CHANNEL_INFO;
        event.port = port;
        event.sessionId = sessionId;
        return event;
    }

    public int getType() {
        return type;
    }

    public String getText() {
        return text;
    }

    public long getSequence() {
        return sequence;
    }

    public int getId() {
        return id;
    }

    public byte[] getData() {
        return data;
    }

    public List<DeviceApp> getApps() {
        return apps;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public int getPort() {
        return port;
    }

    public int getSessionId() {
        return sessionId;
    }
}
