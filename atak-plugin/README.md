# Hydra ATAK Plugin

ATAK plugin that adds **Lock / Strike / Unlock** radial menu buttons to
Hydra Detect detection markers on the ATAK map.

## What it does

When you tap a Hydra detection marker (UID matching `HYDRA-*-DET-*`), the
radial menu shows custom buttons instead of the default marker menu:

- **Lock** — calls `POST /api/target/lock` on the Hydra API
- **Strike** — calls `POST /api/target/strike` (with confirm=true)
- **Unlock** — calls `POST /api/target/unlock`

The plugin reads the Hydra host URL from its preferences (default:
`http://192.168.0.220:8080`). Configure this in ATAK Settings > Tool
Preferences > Hydra Target Control.

## Prerequisites

- **ATAK-CIV SDK 5.5** from [tak.gov](https://tak.gov) (free account)
- **Android Studio** (Narwhal or later)
- **Developer ATAK APK** from the SDK (Google Play ATAK does NOT load custom plugins)

## Build Instructions

### 1. Download the ATAK-CIV SDK

1. Go to [tak.gov](https://tak.gov) > Products > ATAK-CIV > SDK
2. Extract the SDK zip

### 2. Place this project in the SDK

```
atak-civ-sdk/
  atak-civ/
    plugins/
      hydra-atak-plugin/    <-- copy this entire folder here
        app/
        build.gradle
        settings.gradle
        ...
```

### 3. Use the SDK's build template

Copy the `build.gradle` and `settings.gradle` from the SDK's
`plugin-examples/plugintemplate/` directory, then update:

- `applicationId` → `com.hydra.atak.plugin`
- Package references to match `com.hydra.atak.plugin`

Or adapt the existing `app/build.gradle` to reference the SDK JARs.

### 4. Generate signing keys

```bash
keytool -genkeypair -dname "CN=Hydra,O=SORCC,C=US" \
  -validity 9999 -keystore debug.keystore \
  -alias androiddebugkey -keypass android -storepass android
```

### 5. Configure local.properties

```properties
sdk.dir=/path/to/android/sdk
takDebugKeyFile=/absolute/path/to/debug.keystore
takDebugKeyFilePassword=android
takDebugKeyAlias=androiddebugkey
takDebugKeyPassword=android
```

### 6. Build

```bash
./gradlew assembleCivDebug
```

Output: `app/build/outputs/apk/civ/debug/app-civ-debug.apk`

### 7. Install

1. Install the **developer ATAK APK** from the SDK (not Google Play version)
2. Start ATAK on the device
3. Install the plugin APK:
   ```bash
   adb install -r app-civ-debug.apk
   ```
4. In ATAK, open **Plugin Manager** (jigsaw puzzle icon) and load the plugin

## Configuration

In ATAK: Settings > Tool Preferences > Hydra Target Control

- **Hydra Host URL** — set to your Jetson's IP and port
  - Same WiFi: `http://192.168.0.220:8080`
  - Tailscale: `http://100.109.160.122:8080`

## Project Structure

```
app/src/main/
  assets/
    plugin-api.xml              Plugin descriptor
    menus/
      hydra_target_menu.xml     Radial menu (unlocked)
      hydra_target_locked_menu.xml  Radial menu (locked)
    actions/
      lock_target.xml           Lock broadcast action
      strike_target.xml         Strike broadcast action
      unlock_target.xml         Unlock broadcast action
  java/com/hydra/atak/plugin/
    plugin/
      HydraPluginLifecycle.java Plugin lifecycle
      HydraPluginTool.java      Toolbar descriptor
    HydraMapComponent.java      MapMenuFactory + component
    HydraActionReceiver.java    HTTP API caller
  res/
    xml/hydra_preferences.xml   Settings screen
```
