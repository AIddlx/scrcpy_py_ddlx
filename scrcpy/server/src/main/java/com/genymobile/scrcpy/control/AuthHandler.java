package com.genymobile.scrcpy.control;

import com.genymobile.scrcpy.util.Ln;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Arrays;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Handles HMAC-SHA256 Challenge-Response authentication for network mode.
 *
 * Authentication flow:
 * 1. Server generates 32-byte random challenge and sends to client
 * 2. Client calculates HMAC-SHA256(key, challenge) and sends response
 * 3. Server verifies response and sends result
 *
 * Security properties:
 * - Keys are distributed via ADB (secure channel)
 * - Challenge is random (prevents replay attacks)
 * - HMAC provides cryptographic verification
 */
public final class AuthHandler {

    private static final int AUTH_KEY_SIZE = 32;  // 256 bits
    private static final int CHALLENGE_SIZE = 32;
    private static final int RESPONSE_SIZE = 32;
    private static final int AUTH_TIMEOUT_MS = 5000;

    private AuthHandler() {
        // Not instantiable
    }

    /**
     * Perform authentication on a newly connected socket.
     *
     * @param socket The connected client socket
     * @param authKey The 32-byte authentication key (null to skip auth)
     * @return true if authentication successful or skipped
     * @throws IOException if authentication fails or I/O error
     */
    public static boolean authenticate(Socket socket, byte[] authKey) throws IOException {
        if (authKey == null || authKey.length == 0) {
            Ln.i("No auth key provided, skipping authentication");
            return true;
        }

        if (authKey.length != AUTH_KEY_SIZE) {
            Ln.e("Invalid auth key size: " + authKey.length + " bytes");
            return false;
        }

        try {
            socket.setSoTimeout(AUTH_TIMEOUT_MS);
            OutputStream out = socket.getOutputStream();
            InputStream in = socket.getInputStream();

            // 1. Generate and send challenge
            byte[] challenge = generateChallenge();
            out.write(DeviceMessage.TYPE_CHALLENGE);
            out.write(challenge);
            out.flush();
            Ln.d("Sent authentication challenge (" + CHALLENGE_SIZE + " bytes)");

            // 2. Receive response
            int responseType = in.read();
            if (responseType != ControlMessage.TYPE_RESPONSE) {
                Ln.e("Invalid response type: 0x" + Integer.toHexString(responseType));
                sendAuthResult(out, false, "Invalid response type");
                return false;
            }

            byte[] response = readFully(in, RESPONSE_SIZE);
            Ln.d("Received authentication response (" + RESPONSE_SIZE + " bytes)");

            // 3. Verify response
            byte[] expected = hmacSha256(authKey, challenge);
            boolean success = MessageDigest.isEqual(response, expected);

            // 4. Send result
            if (success) {
                sendAuthResult(out, true, null);
                Ln.i("Client authenticated successfully");
            } else {
                sendAuthResult(out, false, "Invalid credentials");
                Ln.w("Client authentication failed: invalid response");
            }

            return success;

        } catch (java.net.SocketTimeoutException e) {
            Ln.w("Authentication timeout");
            return false;
        }
    }

    /**
     * Generate cryptographically secure random challenge.
     */
    private static byte[] generateChallenge() {
        byte[] challenge = new byte[CHALLENGE_SIZE];
        new SecureRandom().nextBytes(challenge);
        return challenge;
    }

    /**
     * Calculate HMAC-SHA256.
     */
    private static byte[] hmacSha256(byte[] key, byte[] data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(data);
        } catch (Exception e) {
            Ln.e("HMAC calculation failed", e);
            throw new RuntimeException("HMAC calculation failed", e);
        }
    }

    /**
     * Send authentication result to client.
     * Format: [type:1][result:1][error_len:2][error:N]
     * Always sends at least 4 bytes (error_len=0 on success).
     */
    private static void sendAuthResult(OutputStream out, boolean success, String errorMessage) throws IOException {
        out.write(DeviceMessage.TYPE_AUTH_RESULT);
        out.write(success ? 1 : 0);

        // Always send error_len (2 bytes, big-endian)
        if (!success && errorMessage != null) {
            byte[] errorBytes = errorMessage.getBytes(java.nio.charset.StandardCharsets.UTF_8);
            int errorLen = Math.min(errorBytes.length, 65535);
            out.write((errorLen >> 8) & 0xFF);
            out.write(errorLen & 0xFF);
            out.write(errorBytes, 0, errorLen);
        } else {
            // Send error_len=0 on success
            out.write(0);
            out.write(0);
        }

        out.flush();
    }

    /**
     * Read exactly n bytes from input stream.
     */
    private static byte[] readFully(InputStream in, int n) throws IOException {
        byte[] buffer = new byte[n];
        int totalRead = 0;
        while (totalRead < n) {
            int read = in.read(buffer, totalRead, n - totalRead);
            if (read < 0) {
                throw new IOException("Unexpected end of stream after " + totalRead + " bytes");
            }
            totalRead += read;
        }
        return buffer;
    }
}
