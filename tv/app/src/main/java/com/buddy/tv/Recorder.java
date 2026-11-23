package com.buddy.tv;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Records from the TV's microphone with Android itself: 16 kHz mono, as a WAV. */
final class Recorder {
    private static final int RATE = 16000;
    private static final int MAX_BYTES = RATE * 2 * 25;  // 25 seconds at most
    private AudioRecord record;
    private Thread thread;
    private final ByteArrayOutputStream pcm = new ByteArrayOutputStream();

    /** "ok", or why it couldn't start. */
    synchronized String start() {
        stopQuietly();
        pcm.reset();
        int size = Math.max(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT), RATE / 5);
        // The kinds of microphone input to try, best for speech first.
        int[] sources = {MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC,
                MediaRecorder.AudioSource.DEFAULT, MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                MediaRecorder.AudioSource.CAMCORDER};
        String why = "no microphone would open";
        for (int src : sources) {
            try {
                AudioRecord r = new AudioRecord(src, RATE, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, size * 2);
                if (r.getState() != AudioRecord.STATE_INITIALIZED) {
                    r.release();
                    continue;
                }
                r.startRecording();
                if (r.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                    r.release();
                    why = "the microphone is busy (Google Assistant listening?)";
                    continue;
                }
                record = r;
                break;
            } catch (Exception e) {
                why = e.getClass().getSimpleName() + ": " + e.getMessage();
            }
        }
        if (record == null) return why;
        final AudioRecord r = record;
        thread = new Thread(() -> {
            byte[] buf = new byte[size];
            while (!Thread.currentThread().isInterrupted()) {
                int n = r.read(buf, 0, buf.length);
                if (n <= 0) break;
                synchronized (pcm) {
                    if (pcm.size() < MAX_BYTES) pcm.write(buf, 0, n);
                }
            }
        }, "buddy-mic");
        thread.start();
        return "ok";
    }

    /** What was recorded, as a WAV in base64 ("" if nothing). */
    synchronized String stop() {
        stopQuietly();
        byte[] data;
        synchronized (pcm) {
            data = pcm.toByteArray();
        }
        if (data.length == 0) return "";
        ByteBuffer h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        h.put("RIFF".getBytes()).putInt(36 + data.length).put("WAVE".getBytes());
        h.put("fmt ".getBytes()).putInt(16).putShort((short) 1).putShort((short) 1)
                .putInt(RATE).putInt(RATE * 2).putShort((short) 2).putShort((short) 16);
        h.put("data".getBytes()).putInt(data.length);
        byte[] wav = new byte[44 + data.length];
        System.arraycopy(h.array(), 0, wav, 0, 44);
        System.arraycopy(data, 0, wav, 44, data.length);
        return Base64.encodeToString(wav, Base64.NO_WRAP);
    }

    private void stopQuietly() {
        if (record != null) {
            try {
                record.stop();
            } catch (Exception ignored) {
                // already stopped
            }
        }
        if (thread != null) {
            try {
                thread.join(500);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            thread = null;
        }
        if (record != null) {
            record.release();
            record = null;
        }
    }
}
