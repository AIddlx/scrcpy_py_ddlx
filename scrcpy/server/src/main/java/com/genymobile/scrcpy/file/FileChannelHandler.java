package com.genymobile.scrcpy.file;

import com.genymobile.scrcpy.util.Ln;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.*;
import java.nio.charset.StandardCharsets;

/**
 * Handles file channel commands from clients.
 */
public class FileChannelHandler {

    // PUSH state (simplified: single file at a time)
    private static String currentPushPath = null;
    private static FileOutputStream currentPushStream = null;
    private static long currentPushTotal = 0;
    private static long currentPushReceived = 0;

    /**
     * Handle a file command.
     *
     * @param cmd     Command type
     * @param payload Command payload
     * @param output  Output stream to write response
     */
    public static void handle(int cmd, byte[] payload, DataOutputStream output) throws IOException {
        try {
            switch (cmd) {
                case FileCommands.CMD_LIST:
                    handleList(payload, output);
                    break;
                case FileCommands.CMD_PULL:
                    handlePull(payload, output);
                    break;
                case FileCommands.CMD_PUSH:
                    handlePushStart(payload, output);
                    break;
                case FileCommands.CMD_PUSH_DATA:
                    handlePushData(payload, output);
                    break;
                case FileCommands.CMD_DELETE:
                    handleDelete(payload, output);
                    break;
                case FileCommands.CMD_MKDIR:
                    handleMkdir(payload, output);
                    break;
                case FileCommands.CMD_STAT:
                    handleStat(payload, output);
                    break;
                default:
                    sendError(output, "Unknown command: " + cmd);
            }
        } catch (JSONException e) {
            sendError(output, "JSON error: " + e.getMessage());
        }
    }

    // === Command handlers ===

    private static void handleList(byte[] payload, DataOutputStream output) throws IOException, JSONException {
        String path = new String(payload, StandardCharsets.UTF_8);
        File dir = new File(path);

        JSONObject result = new JSONObject();
        result.put("path", path);

        JSONArray entries = new JSONArray();
        File[] files = dir.listFiles();

        if (files != null) {
            for (File file : files) {
                JSONObject entry = new JSONObject();
                entry.put("name", file.getName());
                entry.put("type", file.isDirectory() ? "directory" : "file");
                entry.put("size", file.length());
                entry.put("mtime", file.lastModified());
                entries.put(entry);
            }
        }
        result.put("entries", entries);

        sendResponse(output, FileCommands.CMD_LIST_RESP,
                     result.toString().getBytes(StandardCharsets.UTF_8));
        Ln.d("FileServer: LIST " + path + " -> " + (files != null ? files.length : 0) + " entries");
    }

    private static void handlePull(byte[] payload, DataOutputStream output) throws IOException {
        String path = new String(payload, StandardCharsets.UTF_8);
        File file = new File(path);

        if (!file.exists()) {
            sendError(output, "File not found: " + path);
            return;
        }

        if (!file.isFile()) {
            sendError(output, "Not a file: " + path);
            return;
        }

        if (!file.canRead()) {
            sendError(output, "Cannot read file: " + path);
            return;
        }

        long totalSize = file.length();
        byte[] buffer = new byte[FileCommands.CHUNK_SIZE];

        try (FileInputStream fis = new FileInputStream(file)) {
            int chunkId = 0;
            int bytesRead;

            while ((bytesRead = fis.read(buffer)) != -1) {
                // Frame format: [chunk_id:4B][total_size:8B][data:N]
                ByteArrayOutputStream bos = new ByteArrayOutputStream();
                DataOutputStream frame = new DataOutputStream(bos);
                frame.writeInt(chunkId);
                frame.writeLong(totalSize);
                frame.write(buffer, 0, bytesRead);

                sendResponse(output, FileCommands.CMD_PULL_DATA, bos.toByteArray());
                chunkId++;
            }
        }

        Ln.d("FileServer: PULL " + path + " -> " + totalSize + " bytes");
    }

    private static void handlePushStart(byte[] payload, DataOutputStream output) throws IOException {
        // Format: [total_size:8B][path_len:2B][path:N]
        if (payload.length < 10) {
            sendError(output, "Invalid PUSH payload");
            return;
        }

        DataInputStream input = new DataInputStream(new ByteArrayInputStream(payload));

        long totalSize = input.readLong();
        int pathLen = input.readUnsignedShort();
        byte[] pathBytes = new byte[pathLen];
        input.readFully(pathBytes);
        String path = new String(pathBytes, StandardCharsets.UTF_8);

        // Close previous push if any
        closeCurrentPush();

        // Create parent directories
        File file = new File(path);
        File parentDir = file.getParentFile();
        if (parentDir != null && !parentDir.exists()) {
            parentDir.mkdirs();
        }

        try {
            currentPushStream = new FileOutputStream(path);
            currentPushPath = path;
            currentPushTotal = totalSize;
            currentPushReceived = 0;

            sendPushAck(output, 0);
            Ln.d("FileServer: PUSH start " + path + ", total=" + totalSize);
        } catch (FileNotFoundException e) {
            sendError(output, "Cannot create file: " + e.getMessage());
        }
    }

    private static void handlePushData(byte[] payload, DataOutputStream output) throws IOException {
        if (currentPushStream == null) {
            sendError(output, "No active push session");
            return;
        }

        if (payload.length < 4) {
            sendError(output, "Invalid PUSH_DATA payload");
            return;
        }

        // Format: [chunk_id:4B][data:N]
        DataInputStream input = new DataInputStream(new ByteArrayInputStream(payload));
        int chunkId = input.readInt();
        byte[] data = new byte[payload.length - 4];
        input.readFully(data);

        currentPushStream.write(data);
        currentPushReceived += data.length;

        sendPushAck(output, chunkId);

        // Check if complete
        if (currentPushReceived >= currentPushTotal) {
            closeCurrentPush();
            Ln.d("FileServer: PUSH complete " + currentPushPath +
                 ", received=" + currentPushReceived);
        }
    }

    private static void handleDelete(byte[] payload, DataOutputStream output) throws IOException, JSONException {
        String path = new String(payload, StandardCharsets.UTF_8);
        File file = new File(path);

        boolean success = false;
        if (file.exists()) {
            success = deleteRecursively(file);
        }

        JSONObject result = new JSONObject();
        result.put("path", path);
        result.put("success", success);

        sendResponse(output, FileCommands.CMD_STAT_RESP,
                     result.toString().getBytes(StandardCharsets.UTF_8));
        Ln.d("FileServer: DELETE " + path + " -> " + success);
    }

    private static void handleMkdir(byte[] payload, DataOutputStream output) throws IOException, JSONException {
        String path = new String(payload, StandardCharsets.UTF_8);
        File dir = new File(path);

        boolean success = dir.exists() || dir.mkdirs();

        JSONObject result = new JSONObject();
        result.put("path", path);
        result.put("success", success);

        sendResponse(output, FileCommands.CMD_STAT_RESP,
                     result.toString().getBytes(StandardCharsets.UTF_8));
        Ln.d("FileServer: MKDIR " + path + " -> " + success);
    }

    private static void handleStat(byte[] payload, DataOutputStream output) throws IOException, JSONException {
        String path = new String(payload, StandardCharsets.UTF_8);
        File file = new File(path);

        JSONObject result = new JSONObject();
        result.put("path", path);
        result.put("exists", file.exists());

        if (file.exists()) {
            result.put("type", file.isDirectory() ? "directory" : "file");
            result.put("size", file.length());
            result.put("mtime", file.lastModified());
            result.put("canRead", file.canRead());
            result.put("canWrite", file.canWrite());
        }

        sendResponse(output, FileCommands.CMD_STAT_RESP,
                     result.toString().getBytes(StandardCharsets.UTF_8));
        Ln.d("FileServer: STAT " + path);
    }

    // === Utility methods ===

    private static void sendResponse(DataOutputStream output, int cmd, byte[] data) throws IOException {
        output.writeByte(cmd);
        output.writeInt(data.length);
        output.write(data);
        output.flush();
    }

    private static void sendError(DataOutputStream output, String message) throws IOException {
        Ln.w("FileServer: ERROR - " + message);
        sendResponse(output, FileCommands.CMD_ERROR,
                     message.getBytes(StandardCharsets.UTF_8));
    }

    private static void sendPushAck(DataOutputStream output, int chunkId) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        DataOutputStream frame = new DataOutputStream(bos);
        frame.writeInt(chunkId);
        frame.writeByte(0); // status = OK

        sendResponse(output, FileCommands.CMD_PUSH_ACK, bos.toByteArray());
    }

    private static void closeCurrentPush() {
        if (currentPushStream != null) {
            try {
                currentPushStream.close();
            } catch (IOException e) {
                // Ignore
            }
            currentPushStream = null;
        }
        currentPushPath = null;
        currentPushTotal = 0;
        currentPushReceived = 0;
    }

    private static boolean deleteRecursively(File file) {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteRecursively(child);
                }
            }
        }
        return file.delete();
    }
}
