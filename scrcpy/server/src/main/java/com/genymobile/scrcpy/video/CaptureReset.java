package com.genymobile.scrcpy.video;

import android.media.MediaCodec;
import android.os.Bundle;

import java.util.concurrent.atomic.AtomicBoolean;

public class CaptureReset implements SurfaceCapture.CaptureListener {

    private final AtomicBoolean reset = new AtomicBoolean();
    private final AtomicBoolean syncFrameRequested = new AtomicBoolean();

    // Current instance of MediaCodec to "interrupt" on reset
    private MediaCodec runningMediaCodec;

    public boolean consumeReset() {
        return reset.getAndSet(false);
    }

    /**
     * Check if a sync frame (I-frame) was requested and clear the flag.
     */
    public boolean consumeSyncFrameRequest() {
        return syncFrameRequested.getAndSet(false);
    }

    public synchronized void reset() {
        reset.set(true);
        if (runningMediaCodec != null) {
            try {
                runningMediaCodec.signalEndOfInputStream();
            } catch (IllegalStateException e) {
                // ignore
            }
        }
    }

    /**
     * Request an immediate sync frame (I-frame) from the encoder.
     * This is faster than waiting for the next scheduled I-frame.
     */
    public synchronized void requestSyncFrame() {
        syncFrameRequested.set(true);
        if (runningMediaCodec != null) {
            try {
                Bundle params = new Bundle();
                params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 1);
                runningMediaCodec.setParameters(params);
                android.util.Log.i("scrcpy", "Sync frame (I-frame) requested");
            } catch (IllegalStateException e) {
                // MediaCodec may not be in the right state
                android.util.Log.w("scrcpy", "Failed to request sync frame: " + e.getMessage());
            }
        }
    }

    public synchronized void setRunningMediaCodec(MediaCodec runningMediaCodec) {
        this.runningMediaCodec = runningMediaCodec;
    }

    @Override
    public void onInvalidated() {
        reset();
    }
}
