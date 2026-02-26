package com.genymobile.scrcpy.file;

import com.genymobile.scrcpy.util.Ln;

import java.io.*;
import java.net.*;
import java.nio.channels.*;
import java.util.concurrent.*;

/**
 * Independent TCP file server for file transfer operations.
 * Listens on a random port and handles file commands from clients.
 */
public class FileServer {

    private static final int BACKLOG = 1;

    private ServerSocketChannel serverChannel;
    private ExecutorService executor;
    private volatile boolean running = false;
    private int sessionId;

    public FileServer() {
        // Generate a session ID for this server instance
        this.sessionId = (int) (System.currentTimeMillis() & 0x7FFFFFFF);
    }

    /**
     * Start the file server.
     *
     * @return the port number the server is listening on
     * @throws IOException if the server cannot be started
     */
    public int start() throws IOException {
        serverChannel = ServerSocketChannel.open();
        serverChannel.socket().bind(new InetSocketAddress(0), BACKLOG);
        serverChannel.configureBlocking(true);

        int port = serverChannel.socket().getLocalPort();
        running = true;

        executor = Executors.newCachedThreadPool(r -> {
            Thread t = new Thread(r, "file-client-handler");
            t.setDaemon(true);
            return t;
        });

        // Start accept loop in a separate thread
        Thread acceptThread = new Thread(this::acceptLoop, "file-server-accept");
        acceptThread.setDaemon(true);
        acceptThread.start();

        Ln.i("FileServer started on port " + port + ", sessionId=" + sessionId);
        return port;
    }

    /**
     * Stop the file server and release resources.
     */
    public void stop() {
        running = false;

        try {
            if (serverChannel != null) {
                serverChannel.close();
            }
        } catch (IOException e) {
            // Ignore
        }

        if (executor != null) {
            executor.shutdownNow();
        }

        Ln.i("FileServer stopped");
    }

    /**
     * Get the session ID for this server.
     * Clients must send this ID to authenticate.
     */
    public int getSessionId() {
        return sessionId;
    }

    private void acceptLoop() {
        while (running) {
            try {
                SocketChannel client = serverChannel.accept();
                if (client != null) {
                    executor.submit(() -> handleClient(client));
                }
            } catch (IOException e) {
                if (running) {
                    Ln.e("FileServer accept error", e);
                }
            }
        }
    }

    private void handleClient(SocketChannel client) {
        String clientInfo = client.socket().getInetAddress().toString();

        try (SocketChannel ch = client) {
            ch.configureBlocking(true);

            DataInputStream input = new DataInputStream(
                new BufferedInputStream(Channels.newInputStream(ch)));
            DataOutputStream output = new DataOutputStream(
                new BufferedOutputStream(Channels.newOutputStream(ch)));

            // Read and validate session_id
            int clientSessionId = input.readInt();

            if (clientSessionId != sessionId) {
                Ln.w("FileServer: invalid session_id " + clientSessionId +
                     " from " + clientInfo + " (expected " + sessionId + ")");
                return;
            }

            Ln.d("FileServer: client connected from " + clientInfo);

            // Process commands
            while (running && ch.isConnected()) {
                try {
                    // Read command frame: [cmd:1B][length:4B][payload:N]
                    int cmd = input.readUnsignedByte();
                    int length = input.readInt();

                    byte[] payload = new byte[length];
                    if (length > 0) {
                        input.readFully(payload);
                    }

                    // Handle command
                    FileChannelHandler.handle(cmd, payload, output);

                } catch (EOFException e) {
                    // Client disconnected
                    break;
                } catch (SocketException e) {
                    // Connection reset
                    break;
                }
            }
        } catch (IOException e) {
            Ln.d("FileServer client error from " + clientInfo + ": " + e.getMessage());
        }

        Ln.d("FileServer: client disconnected from " + clientInfo);
    }
}
