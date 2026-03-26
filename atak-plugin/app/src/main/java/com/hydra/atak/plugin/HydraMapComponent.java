package com.hydra.atak.plugin;

import android.content.Context;
import android.content.Intent;
import android.util.Log;

import com.atakmap.android.dropdown.DropDownMapComponent;
import com.atakmap.android.ipc.AtakBroadcast.DocumentedIntentFilter;
import com.atakmap.android.maps.MapItem;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.menu.MapMenuFactory;
import com.atakmap.android.menu.MapMenuReceiver;
import com.atakmap.android.menu.MenuMapAdapter;
import com.atakmap.android.menu.PluginMenuParser;
import com.atakmap.android.widgets.MapWidget;

import java.util.HashSet;
import java.util.Set;

/**
 * Core plugin component. Registers a {@link MapMenuFactory} that intercepts
 * radial menu display for Hydra detection markers (UID matching
 * {@code HYDRA-*-DET-*}) and shows Lock/Strike/Unlock actions.
 */
public class HydraMapComponent extends DropDownMapComponent
        implements MapMenuFactory {

    private static final String TAG = "HydraPlugin";
    private MapView mapView;
    private Context pluginContext;
    private HydraActionReceiver actionReceiver;
    private final Set<String> lockedUIDs = new HashSet<>();

    @Override
    public void onCreate(Context context, Intent intent, MapView view) {
        context.setTheme(R.style.ATAKPluginTheme);
        super.onCreate(context, intent, view);
        this.pluginContext = context;
        this.mapView = view;

        // Register as a MapMenuFactory (highest priority — index 0)
        MapMenuReceiver.getInstance().registerMapMenuFactory(this);
        Log.i(TAG, "Hydra MapMenuFactory registered");

        // Register action receiver for radial menu button clicks
        actionReceiver = new HydraActionReceiver(view, this);
        DocumentedIntentFilter filter = new DocumentedIntentFilter();
        filter.addAction(HydraActionReceiver.ACTION_LOCK);
        filter.addAction(HydraActionReceiver.ACTION_STRIKE);
        filter.addAction(HydraActionReceiver.ACTION_UNLOCK);
        registerDropDownReceiver(actionReceiver, filter);
        Log.i(TAG, "Hydra Target Control plugin started");
    }

    @Override
    public MapWidget create(MapItem item) {
        if (item == null) return null;

        String uid = item.getUID();
        if (uid == null || !isHydraDetectionMarker(uid)) {
            return null; // Fall through to default menu factory
        }

        boolean isLocked = lockedUIDs.contains(uid);
        String menuAsset = isLocked
                ? "menus/hydra_target_locked_menu.xml"
                : "menus/hydra_target_menu.xml";

        try {
            return PluginMenuParser.getMenuWidget(
                    pluginContext, mapView, menuAsset, item);
        } catch (Exception e) {
            Log.e(TAG, "Failed to create radial menu for " + uid, e);
            return null;
        }
    }

    /**
     * Check if a UID matches the Hydra detection marker pattern.
     * Expected format: HYDRA-{callsign}-DET-{track_id}
     */
    private boolean isHydraDetectionMarker(String uid) {
        // Match: starts with HYDRA- and contains -DET-
        return uid.startsWith("HYDRA-") && uid.contains("-DET-");
    }

    /** Mark a target UID as locked (changes radial menu to show unlock). */
    void setLocked(String uid) {
        lockedUIDs.add(uid);
    }

    /** Mark a target UID as unlocked. */
    void setUnlocked(String uid) {
        lockedUIDs.remove(uid);
    }

    /** Clear all locked state (e.g., on global unlock). */
    void clearAllLocks() {
        lockedUIDs.clear();
    }

    @Override
    protected void onDestroyImpl(Context context, MapView view) {
        MapMenuReceiver.getInstance().unregisterMapMenuFactory(this);
        Log.i(TAG, "Hydra MapMenuFactory unregistered");
        super.onDestroyImpl(context, view);
    }
}
