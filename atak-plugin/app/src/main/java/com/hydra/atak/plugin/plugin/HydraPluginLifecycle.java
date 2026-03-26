package com.hydra.atak.plugin.plugin;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;

import com.atakmap.android.maps.MapView;
import com.hydra.atak.plugin.HydraMapComponent;

import java.util.Collection;
import java.util.Collections;
import java.util.LinkedList;

import transapps.maps.plugin.lifecycle.Lifecycle;
import transapps.mapi.MapView as TransMapView;

/**
 * Plugin lifecycle — creates and destroys the HydraMapComponent.
 */
public class HydraPluginLifecycle implements Lifecycle {

    private final Context pluginContext;
    private final Collection<com.atakmap.android.maps.MapComponent> overlays;
    private MapView mapView;

    public HydraPluginLifecycle(Context ctx) {
        this.pluginContext = ctx;
        this.overlays = new LinkedList<>();
    }

    @Override
    public void onConfigurationChanged(Configuration configuration) {
        for (com.atakmap.android.maps.MapComponent c : overlays) {
            c.onConfigurationChanged(configuration);
        }
    }

    @Override
    public void onCreate(final Activity activity, final TransMapView view) {
        if (view instanceof MapView) {
            mapView = (MapView) view;
        } else {
            mapView = MapView.getMapView();
        }
        HydraMapComponent component = new HydraMapComponent();
        overlays.add(component);
        component.onCreate(pluginContext,
                activity.getIntent(), mapView);
    }

    @Override
    public void onDestroy() {
        for (com.atakmap.android.maps.MapComponent c : overlays) {
            c.onDestroy(pluginContext, mapView);
        }
        overlays.clear();
    }

    @Override
    public void onFinish() { }

    @Override
    public void onPause() {
        for (com.atakmap.android.maps.MapComponent c : overlays) {
            c.onPause(pluginContext, mapView);
        }
    }

    @Override
    public void onResume() {
        for (com.atakmap.android.maps.MapComponent c : overlays) {
            c.onResume(pluginContext, mapView);
        }
    }

    @Override
    public void onStart() {
        for (com.atakmap.android.maps.MapComponent c : overlays) {
            c.onStart(pluginContext, mapView);
        }
    }

    @Override
    public void onStop() {
        for (com.atakmap.android.maps.MapComponent c : overlays) {
            c.onStop(pluginContext, mapView);
        }
    }
}
