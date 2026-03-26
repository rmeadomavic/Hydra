package com.hydra.atak.plugin;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.preference.PreferenceManager;
import android.util.Log;
import android.widget.Toast;

import com.atakmap.android.dropdown.DropDownReceiver;
import com.atakmap.android.maps.MapView;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Receives radial menu action broadcasts and calls the Hydra HTTP API
 * to lock, strike, or unlock targets.
 */
public class HydraActionReceiver extends DropDownReceiver {

    private static final String TAG = "HydraPlugin";

    static final String ACTION_LOCK =
            "com.hydra.atak.plugin.LOCK_TARGET";
    static final String ACTION_STRIKE =
            "com.hydra.atak.plugin.STRIKE_TARGET";
    static final String ACTION_UNLOCK =
            "com.hydra.atak.plugin.UNLOCK_TARGET";

    private final HydraMapComponent component;

    public HydraActionReceiver(MapView mapView, HydraMapComponent component) {
        super(mapView);
        this.component = component;
    }

    @Override
    public void disposeImpl() { }

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (action == null) return;

        String targetUID = intent.getStringExtra("targetUID");
        if (targetUID == null) {
            Log.w(TAG, "No targetUID in intent");
            return;
        }

        int trackId = extractTrackId(targetUID);
        if (trackId < 0) {
            Log.w(TAG, "Could not extract track_id from UID: " + targetUID);
            return;
        }

        String baseUrl = getHydraBaseUrl();

        switch (action) {
            case ACTION_LOCK:
                Log.i(TAG, "LOCK target #" + trackId + " (" + targetUID + ")");
                postAsync(baseUrl + "/api/target/lock",
                        "{\"track_id\": " + trackId + "}",
                        "Lock #" + trackId);
                component.setLocked(targetUID);
                break;

            case ACTION_STRIKE:
                Log.i(TAG, "STRIKE target #" + trackId + " (" + targetUID + ")");
                postAsync(baseUrl + "/api/target/strike",
                        "{\"track_id\": " + trackId + ", \"confirm\": true}",
                        "Strike #" + trackId);
                break;

            case ACTION_UNLOCK:
                Log.i(TAG, "UNLOCK (" + targetUID + ")");
                postAsync(baseUrl + "/api/target/unlock", "{}",
                        "Unlock");
                component.clearAllLocks();
                break;

            default:
                Log.w(TAG, "Unknown action: " + action);
        }
    }

    /**
     * Extract track_id from UID format: HYDRA-{callsign}-DET-{track_id}
     */
    private int extractTrackId(String uid) {
        try {
            String[] parts = uid.split("-");
            return Integer.parseInt(parts[parts.length - 1]);
        } catch (NumberFormatException | ArrayIndexOutOfBoundsException e) {
            return -1;
        }
    }

    private String getHydraBaseUrl() {
        SharedPreferences prefs = PreferenceManager
                .getDefaultSharedPreferences(getMapView().getContext());
        return prefs.getString("hydra_host_url", "http://192.168.0.220:8080");
    }

    /**
     * POST JSON to the Hydra API on a background thread.
     * Shows a toast on success or failure.
     */
    private void postAsync(String urlStr, String json, String label) {
        new Thread(() -> {
            try {
                URL url = new URL(urlStr);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(json.getBytes(StandardCharsets.UTF_8));
                }
                int code = conn.getResponseCode();
                conn.disconnect();

                String msg = code == 200
                        ? "Hydra: " + label + " OK"
                        : "Hydra: " + label + " failed (" + code + ")";
                Log.i(TAG, msg);
                showToast(msg);
            } catch (IOException e) {
                String msg = "Hydra: " + label + " error - " + e.getMessage();
                Log.e(TAG, msg, e);
                showToast(msg);
            }
        }, "hydra-api-" + label).start();
    }

    private void showToast(String message) {
        getMapView().post(() ->
                Toast.makeText(getMapView().getContext(), message,
                        Toast.LENGTH_SHORT).show());
    }
}
