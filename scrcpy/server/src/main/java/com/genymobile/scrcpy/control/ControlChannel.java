package com.genymobile.scrcpy.control;

import android.net.LocalSocket;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;

public final class ControlChannel {

    private final ControlMessageReader reader;
    private final DeviceMessageWriter writer;

    public ControlChannel(LocalSocket controlSocket) throws IOException {
        reader = new ControlMessageReader(controlSocket.getInputStream());
        writer = new DeviceMessageWriter(controlSocket.getOutputStream());
    }

    // Network mode constructor (TCP Socket)
    public ControlChannel(Socket controlSocket) throws IOException {
        reader = new ControlMessageReader(controlSocket.getInputStream());
        writer = new DeviceMessageWriter(controlSocket.getOutputStream());
    }

    // Generic constructor using streams
    public ControlChannel(InputStream inputStream, OutputStream outputStream) throws IOException {
        reader = new ControlMessageReader(inputStream);
        writer = new DeviceMessageWriter(outputStream);
    }

    public ControlMessage recv() throws IOException {
        return reader.read();
    }

    public void send(DeviceMessage msg) throws IOException {
        writer.write(msg);
    }
}
