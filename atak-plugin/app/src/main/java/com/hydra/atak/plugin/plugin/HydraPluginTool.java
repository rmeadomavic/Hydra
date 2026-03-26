package com.hydra.atak.plugin.plugin;

import android.content.Context;

import com.hydra.atak.plugin.R;

import transapps.maps.plugin.tool.Group;
import transapps.maps.plugin.tool.ToolDescriptor;

/**
 * Toolbar descriptor — registers the plugin icon in ATAK's tool list.
 */
public class HydraPluginTool extends ToolDescriptor {

    private final Context context;

    public HydraPluginTool(Context context) {
        this.context = context;
    }

    @Override
    public String getDescription() {
        return context.getString(R.string.app_desc);
    }

    @Override
    public String getIconUri() {
        return "android.resource://" + context.getPackageName() + "/"
                + R.drawable.ic_hydra;
    }

    @Override
    public String getShortDescription() {
        return context.getString(R.string.app_name);
    }

    @Override
    public Group[] getGroups() {
        return new Group[]{ Group.GENERAL };
    }
}
