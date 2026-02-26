package com.genymobile.scrcpy.file;

/**
 * File channel command constants.
 */
public final class FileCommands {

    // Client -> Server commands
    public static final int CMD_LIST = 1;           // List directory
    public static final int CMD_PULL = 3;           // Download file
    public static final int CMD_PUSH = 5;           // Start upload
    public static final int CMD_PUSH_DATA = 6;      // Upload data chunk
    public static final int CMD_DELETE = 8;         // Delete file/directory
    public static final int CMD_MKDIR = 9;          // Create directory
    public static final int CMD_STAT = 10;          // Get file info

    // Server -> Client responses
    public static final int CMD_LIST_RESP = 2;      // Directory list response
    public static final int CMD_PULL_DATA = 4;      // File data chunk
    public static final int CMD_PUSH_ACK = 7;       // Upload acknowledgment
    public static final int CMD_STAT_RESP = 11;     // File info response
    public static final int CMD_ERROR = 255;        // Error response

    // Chunk size for file transfer
    public static final int CHUNK_SIZE = 64 * 1024; // 64KB

    private FileCommands() {
        // Utility class, no instances
    }
}
