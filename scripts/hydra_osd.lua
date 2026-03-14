--[[
  Hydra Detect — ArduPilot Lua OSD Script
  ========================================
  Receives detection telemetry from the Jetson companion computer via
  NAMED_VALUE_FLOAT / NAMED_VALUE_INT MAVLink messages and displays it
  on the FC's onboard OSD (MAX7456 / AT7456E).

  Installation:
    1. Copy this file to the FC SD card: APM/scripts/hydra_osd.lua
    2. Set these ArduPilot parameters and reboot:
         SCR_ENABLE    = 1
         SCR_HEAP_SIZE = 65536  (or higher if other scripts are running)
         OSD_TYPE      = 1      (MAX7456 onboard)
         OSD1_ENABLE   = 1
         OSD1_MESSAGE_EN = 1    (enable message panel)
    3. In Hydra config.ini set:
         [osd]
         enabled = true
         mode = named_value

  Compatible boards: Matek H743, SpeedyBee F405-Wing, any FC with AT7456E/MAX7456.
  NOT compatible with Pixhawk 6C (no onboard OSD chip).

  Expected NAMED_VALUE messages from Jetson:
    osd_fps    (float)  — detection pipeline FPS
    osd_infms  (float)  — inference time in milliseconds
    osd_trks   (int)    — number of active tracks
    osd_lkid   (int)    — locked track ID (-1 = no lock)
    osd_lkmod  (int)    — lock mode (1=track, 2=strike)
    osd_gfix   (int)    — GPS fix type
--]]

local mavlink_msgs = require("mavlink_msgs")

-- MAVLink message IDs
local NAMED_VALUE_FLOAT_ID = mavlink_msgs.get_msgid("NAMED_VALUE_FLOAT")
local NAMED_VALUE_INT_ID   = mavlink_msgs.get_msgid("NAMED_VALUE_INT")

-- Decoder message map
local msg_map = {}
msg_map[NAMED_VALUE_FLOAT_ID] = "NAMED_VALUE_FLOAT"
msg_map[NAMED_VALUE_INT_ID]   = "NAMED_VALUE_INT"

-- State variables
local osd_fps    = 0.0
local osd_infms  = 0.0
local osd_trks   = 0
local osd_lkid   = -1
local osd_lkmod  = 0
local osd_gfix   = 0
local last_rx     = 0  -- millis() of last received message

-- How long before we consider the Jetson link stale (ms)
local STALE_TIMEOUT_MS = 3000

-- Initialise MAVLink message reception
mavlink:init(1, 10)
mavlink:register_rx_msgid(NAMED_VALUE_FLOAT_ID)
mavlink:register_rx_msgid(NAMED_VALUE_INT_ID)

-- Trim trailing nulls from MAVLink name field
local function trim_name(raw)
    if not raw then return "" end
    return raw:match("^(.-)%z*$") or raw
end

-- Process incoming MAVLink messages (drain queue each tick)
local function process_mavlink()
    for _ = 1, 20 do  -- drain up to 20 per tick to avoid starvation
        local msg, chan = mavlink:receive_chan()
        if not msg then return end

        local parsed = mavlink_msgs.decode(msg, msg_map)
        if not parsed then return end

        local name = trim_name(parsed.name)
        last_rx = millis():toint()

        if parsed.msgid == NAMED_VALUE_FLOAT_ID then
            if     name == "osd_fps"   then osd_fps   = parsed.value
            elseif name == "osd_infms" then osd_infms = parsed.value
            end
        elseif parsed.msgid == NAMED_VALUE_INT_ID then
            if     name == "osd_trks"  then osd_trks  = parsed.value
            elseif name == "osd_lkid"  then osd_lkid  = parsed.value
            elseif name == "osd_lkmod" then osd_lkmod = parsed.value
            elseif name == "osd_gfix"  then osd_gfix  = parsed.value
            end
        end
    end
end

-- Format the OSD display string and send via notify
local function update_display()
    local now = millis():toint()
    local stale = (now - last_rx) > STALE_TIMEOUT_MS

    if stale and last_rx > 0 then
        -- Jetson link lost — show warning
        notify:send_text(0, "HYDRA: NO LINK")
        return
    end

    if last_rx == 0 then
        -- Never received data yet
        notify:send_text(0, "HYDRA: WAITING")
        return
    end

    -- Build display string (MAX7456 is 30 chars wide)
    local line1 = string.format("T:%d %dfps %dms", osd_trks, osd_fps, osd_infms)

    if osd_lkid >= 0 then
        local mode_str = "TRK"
        if osd_lkmod == 2 then mode_str = "STK" end
        line1 = line1 .. string.format(" LK#%d%s", osd_lkid, mode_str)
    end

    -- Slot 0: main detection info
    notify:send_text(0, line1)
end

-- Main update function — called every 200ms
function update()
    process_mavlink()
    update_display()
    return update, 200
end

gcs:send_text(6, "Hydra OSD script loaded")
return update, 1000  -- first call after 1 second
