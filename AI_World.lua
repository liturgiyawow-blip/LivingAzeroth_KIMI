--[[
    Living Azeroth — AI Bridge (v2.3)
    NO TIMERS. Instant delivery to player. NPC speaks if present.
]]

local AI_WORLD = {
    SEARCH_RADIUS = 30,
    DEBUG = true,
}

local function Log(msg)
    print("[LivingAzeroth] " .. tostring(msg))
end

local function DebugToPlayer(player, msg)
    if AI_WORLD.DEBUG and player then
        player:SendBroadcastMessage("|cff00ccff[AI]|r " .. tostring(msg))
    end
end

local function EscapeSQL(str)
    if not str then return "" end
    return tostring(str):gsub("'", "''"):gsub("\\", "\\\\")
end

-- ============================================
-- WRITE REQUEST
-- ============================================

local function WriteRequestToDB(player, target, message, channelType, targetIsPlayer)
    if not player or not target then
        Log("WriteRequestToDB: missing player or target")
        return false
    end

    local pName = EscapeSQL(player:GetName())
    local pGuid = player:GetGUIDLow()
    local msg = EscapeSQL(message)
    local channel = EscapeSQL(channelType)

    local tName, tGuid, tEntry

    if targetIsPlayer then
        tName = EscapeSQL(target:GetName())
        tGuid = target:GetGUIDLow()
        tEntry = 0
    else
        tName = EscapeSQL(target:GetName())
        tGuid = target:GetGUIDLow()
        tEntry = target:GetEntry() or 0
    end

    local sql = string.format(
        "INSERT INTO ai_requests " ..
        "(player_guid, player_name, npc_guid, npc_entry, npc_name, message, channel_type, target_is_player, created_at) " ..
        "VALUES (%u, '%s', %u, %d, '%s', '%s', '%s', %d, UNIX_TIMESTAMP())",
        pGuid, pName, tGuid, tEntry, tName, msg, channel, targetIsPlayer and 1 or 0
    )

    local status, err = pcall(function() CharDBExecute(sql) end)
    if status then
        Log(string.format("Request queued: %s -> %s [%s]", pName, tName, channel))
        return true
    else
        Log("SQL ERROR: " .. tostring(err))
        return false
    end
end

-- ============================================
-- CHECK FOR RESPONSE (single immediate check)
-- ============================================

local function CheckAndDeliverResponse(player, targetGuid, targetIsPlayer)
    if not player then return false end

    local sql = string.format(
        "SELECT id, response_text, emote_id, action_command FROM ai_responses " ..
        "WHERE player_guid = %u AND npc_guid = %u AND fetched = 0 ORDER BY created_at DESC LIMIT 1",
        player:GetGUIDLow(), targetGuid
    )

    local query = CharDBQuery(sql)
    if not query then
        return false -- No response yet
    end

    local rowId = query:GetUInt32(0)
    local text = query:GetString(1)
    local emoteId = query:GetUInt32(2)
    local actionCmd = query:GetString(3)

    -- Mark as fetched
    CharDBExecute("UPDATE ai_responses SET fetched = 1, delivered_at = UNIX_TIMESTAMP() WHERE id = " .. rowId)

    -- Try to find target
    local target = nil
    if targetIsPlayer then
        target = GetPlayerByGUID(targetGuid)
    else
        local status, result = pcall(function()
            return player:GetMap():GetCreature(targetGuid)
        end)
        if status then target = result end
    end

    -- DELIVER TO PLAYER (always works)
    player:SendBroadcastMessage("|cff00ff00[AI Ответ]|r " .. text)

    -- Try to make target speak (if still valid)
    if targetIsPlayer and target then
        target:Say(text, 0)
        DebugToPlayer(player, "Bot " .. target:GetName() .. " replied")

    elseif target then
        target:SendUnitSay(text, 0)
        if emoteId and emoteId > 0 then target:PerformEmote(emoteId) end
        DebugToPlayer(player, "NPC " .. target:GetName() .. " replied")

    else
        DebugToPlayer(player, "Target despawned, message shown above")
    end

    if actionCmd and actionCmd ~= "" and actionCmd ~= "null" then
        Log("Action command: " .. actionCmd)
    end

    return true
end

-- ============================================
-- SIMPLE POLLING LOOP (global, not per-player)
-- ============================================

local pendingChecks = {} -- {playerGuid = {targetGuid, targetIsPlayer, retries}}

local function GlobalPollLoop()
    for playerGuid, data in pairs(pendingChecks) do
        local player = GetPlayerByGUID(playerGuid)
        if player then
            local success = CheckAndDeliverResponse(player, data.targetGuid, data.targetIsPlayer)
            if success then
                pendingChecks[playerGuid] = nil
            else
                data.retries = data.retries + 1
                if data.retries > 60 then -- 30 seconds timeout
                    player:SendBroadcastMessage("|cffff0000[AI]|r Response timeout.")
                    pendingChecks[playerGuid] = nil
                end
            end
        else
            pendingChecks[playerGuid] = nil -- Player offline
        end
    end
end

-- Start global polling (every 500ms)
CreateLuaEvent(GlobalPollLoop, 500, 0) -- 0 = infinite repeats

-- ============================================
-- CHANNEL HANDLERS
-- ============================================

local function HandleSayChannel(player, msg)
    local creatures = player:GetCreaturesInRange(AI_WORLD.SEARCH_RADIUS)
    local targetNpc = nil

    for _, c in ipairs(creatures) do
        local valid = false
        if c and c.IsAlive and c.GetEntry then
            local status, result = pcall(function() 
                return c:IsAlive() and c:GetEntry() > 0 
            end)
            if status and result then
                valid = true
            end
        end
        
        if valid then
            targetNpc = c
            break
        end
    end

    if not targetNpc then
        DebugToPlayer(player, "No valid NPC found within " .. AI_WORLD.SEARCH_RADIUS .. "m")
        return
    end

    local status, name = pcall(function() return targetNpc:GetName() end)
    if not status then
        DebugToPlayer(player, "NPC became invalid, try again")
        return
    end

    DebugToPlayer(player, "Talking to NPC: " .. name)

    local npcGuid = targetNpc:GetGUIDLow()

    if WriteRequestToDB(player, targetNpc, msg, "SAY", false) then
        DebugToPlayer(player, "Request sent to AI. Waiting...")
        -- Register for global polling
        pendingChecks[player:GetGUIDLow()] = {
            targetGuid = npcGuid,
            targetIsPlayer = false,
            retries = 0
        }
    end
end

local function HandlePartyChannel(player, msg)
    local group = player:GetGroup()
    if not group then
        DebugToPlayer(player, "Not in a group!")
        return
    end

    local members = group:GetMembers()
    local targetBot = nil

    for _, member in ipairs(members) do
        if not member then goto continue end
        local mGuid = member:GetGUIDLow()
        if mGuid == player:GetGUIDLow() then goto continue end

        local mName = member:GetName()
        if msg:lower():find(mName:lower()) or
           msg:lower():find("бро") or
           msg:lower():find("пати") or
           msg:lower():find("все") or
           msg:lower():find("ребята") then
            targetBot = member
            break
        end
        ::continue::
    end

    if not targetBot then
        DebugToPlayer(player, "No bot addressed in party chat")
        return
    end

    DebugToPlayer(player, "Addressing bot: " .. targetBot:GetName())

    local botGuid = targetBot:GetGUIDLow()

    if WriteRequestToDB(player, targetBot, msg, "PARTY", true) then
        DebugToPlayer(player, "Party request sent to AI...")
        pendingChecks[player:GetGUIDLow()] = {
            targetGuid = botGuid,
            targetIsPlayer = true,
            retries = 0
        }
    end
end

local function HandleWhisperChannel(player, msg, targetName)
    if not targetName or targetName == "" then
        DebugToPlayer(player, "Whisper target not found")
        return
    end

    local target = GetPlayerByName(targetName)
    if not target then
        local allPlayers = GetPlayersInWorld()
        for _, p in ipairs(allPlayers) do
            if p:GetName():lower():find(targetName:lower()) then
                target = p
                break
            end
        end
    end

    if not target then
        DebugToPlayer(player, "Bot '" .. targetName .. "' not found")
        return
    end

    if target:GetGUIDLow() == player:GetGUIDLow() then
        DebugToPlayer(player, "Cannot whisper yourself")
        return
    end

    DebugToPlayer(player, "Whispering to: " .. target:GetName())

    local targetGuid = target:GetGUIDLow()

    if WriteRequestToDB(player, target, msg, "WHISPER", true) then
        DebugToPlayer(player, "Whisper request sent to AI...")
        pendingChecks[player:GetGUIDLow()] = {
            targetGuid = targetGuid,
            targetIsPlayer = true,
            retries = 0
        }
    end
end

-- ============================================
-- MAIN HANDLER
-- ============================================

local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    if not msg or #msg < 2 then return end
    if msg:sub(1, 1) == "." then return end

    DebugToPlayer(player, string.format("Chat caught: type=%d, lang=%d, msg='%s'", msgType, lang, msg))

    if msgType == 1 then
        HandleSayChannel(player, msg)
    elseif msgType == 2 then
        HandlePartyChannel(player, msg)
    elseif msgType == 6 then
        HandleWhisperChannel(player, msg, targetName)
    end
    -- Debug: log ALL chat types
    if msgType ~= 1 and msgType ~= 2 and msgType ~= 6 then
    DebugToPlayer(player, "Unknown msgType: " .. msgType)
    end
end

-- ============================================
-- REGISTRATION
-- ============================================

RegisterPlayerEvent(18, OnPlayerChat)
Log("Living Azeroth AI Bridge [v2.3] loaded!")
Log("Channels: SAY | PARTY | WHISPER")
Log("NO TIMERS — global polling loop")