print("[LivingAzeroth] === FILE LOADING ===")

local AI_WORLD = {
    SEARCH_RADIUS = 30,
    FIND_RADIUS   = 100,
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
    return tostring(str):gsub("\0", ""):gsub("'", "''"):gsub("\\", "\\\\")
end

-- ============================================
-- SAFE PLAYER LOOKUP
-- ============================================
local function FindPlayerByGUIDLow(guidLow)
    local players = GetPlayersInWorld()
    if not players then return nil end
    for i = 1, #players do
        local p = players[i]
        if p then
            local ok, low = pcall(function() return p:GetGUIDLow() end)
            if ok and low == guidLow then return p end
        end
    end
    return nil
end

-- ============================================
-- NPC LOOKUP BY GUID LOW (GetCreature wants full GUID)
-- ============================================
local function FindCreatureByGUIDLow(player, guidLow)
    if not player then return nil end
    
    local creatures = player:GetCreaturesInRange(AI_WORLD.FIND_RADIUS)
    if not creatures then return nil end
    
    for i = 1, #creatures do
        local c = creatures[i]
        if c then
            local ok, low = pcall(function() return c:GetGUIDLow() end)
            if ok and low == guidLow then
                return c
            end
        end
    end
    
    -- Fallback: попробуем GetCreature если вдруг сработает
    local ok, result = pcall(function()
        return player:GetMap():GetCreature(guidLow)
    end)
    if ok and result then
        local ok2, low = pcall(function() return result:GetGUIDLow() end)
        if ok2 and low == guidLow then return result end
    end
    
    return nil
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

    local tName = EscapeSQL(target:GetName())
    local tGuid = target:GetGUIDLow()
    local tEntry = 0
    if not targetIsPlayer then
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
-- DELIVER RESPONSE
-- ============================================
local function CheckAndDeliverResponse(playerGuid, playerName, targetGuid, targetIsPlayer, targetName)
    local player = FindPlayerByGUIDLow(playerGuid)
    if not player then
        Log("Player offline: " .. tostring(playerName))
        return true
    end

    local sql = string.format(
        "SELECT id, response_text, emote_id, action_command FROM ai_responses " ..
        "WHERE player_guid = %u AND npc_guid = %u AND fetched = 0 ORDER BY created_at DESC LIMIT 1",
        playerGuid, targetGuid
    )

    local query = CharDBQuery(sql)
    if not query then
        return false
    end

    local rowId     = query:GetUInt32(0)
    local text      = query:GetString(1)
    local emoteId   = query:GetUInt32(2)
    local actionCmd = query:GetString(3)

    CharDBExecute("UPDATE ai_responses SET fetched = 1, delivered_at = UNIX_TIMESTAMP() WHERE id = " .. rowId)

    local target = nil
    if targetIsPlayer then
        target = FindPlayerByGUIDLow(targetGuid)
    else
        target = FindCreatureByGUIDLow(player, targetGuid)
    end

    if target and not targetIsPlayer then
        local sayOk = pcall(function() target:SendUnitSay(text, 0) end)
        if not sayOk then
            player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
        end
        if emoteId and emoteId > 0 then
            pcall(function() target:PerformEmote(emoteId) end)
        end
        DebugToPlayer(player, "NPC " .. targetName .. " replied via Say")

    elseif target and targetIsPlayer then
        local sayOk = pcall(function() target:Say(text, 0) end)
        if not sayOk then
            player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
        end
        DebugToPlayer(player, "Bot " .. targetName .. " replied via Say")

    else
        player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
        DebugToPlayer(player, "Target not found, text shown with name")
    end

    if actionCmd and actionCmd ~= "" and actionCmd ~= "null" then
        Log("Action command: " .. actionCmd)
    end

    return true
end

-- ============================================
-- GLOBAL POLLING
-- ============================================
local pendingChecks = {}
local pollCounter = 0

local function GenerateKey(playerGuid)
    pollCounter = pollCounter + 1
    return string.format("%u_%u", playerGuid, pollCounter)
end

local function GlobalPollLoop()
    for key, data in pairs(pendingChecks) do
        local done = CheckAndDeliverResponse(
            data.playerGuid,
            data.playerName,
            data.targetGuid,
            data.targetIsPlayer,
            data.targetName
        )
        if done then
            pendingChecks[key] = nil
        else
            data.retries = data.retries + 1
            if data.retries > 60 then
                local p = FindPlayerByGUIDLow(data.playerGuid)
                if p then
                    p:SendBroadcastMessage("|cffff0000[AI]|r Response timeout.")
                end
                pendingChecks[key] = nil
            end
        end
    end
end

-- ============================================
-- HANDLE SAY (nearest alive NPC)
-- ============================================
local function HandleSayChannel(player, msg)
    local creatures = player:GetCreaturesInRange(AI_WORLD.SEARCH_RADIUS)
    if not creatures then
        DebugToPlayer(player, "No creatures in range")
        return
    end

    local targetNpc = nil
    local targetName = "Unknown"
    local nearestDist = 999999

    for i = 1, #creatures do
        local c = creatures[i]
        if c then
            local okAlive = pcall(function() return c:IsAlive() end)
            if okAlive and c:IsAlive() then
                local okEntry, entry = pcall(function() return c:GetEntry() end)
                if okEntry and entry and entry > 0 then
                    local okDist, dist = pcall(function() return player:GetDistance(c) end)
                    if okDist and dist and dist < nearestDist then
                        nearestDist = dist
                        targetNpc = c
                        local okName, n = pcall(function() return c:GetName() end)
                        if okName then targetName = n end
                    end
                end
            end
        end
    end

    if not targetNpc then
        DebugToPlayer(player, "No valid NPC within " .. AI_WORLD.SEARCH_RADIUS .. "m")
        return
    end

    local npcGuid = targetNpc:GetGUIDLow()

    if WriteRequestToDB(player, targetNpc, msg, "SAY", false) then
        DebugToPlayer(player, "Talking to NPC: " .. targetName)
        local key = GenerateKey(player:GetGUIDLow())
        pendingChecks[key] = {
            playerGuid     = player:GetGUIDLow(),
            playerName     = player:GetName(),
            targetGuid     = npcGuid,
            targetIsPlayer = false,
            targetName     = targetName,
            retries        = 0,
        }
    end
end

-- ============================================
-- HANDLE PARTY
-- ============================================
local function HandlePartyChannel(player, msg)
    local group = player:GetGroup()
    if not group then
        DebugToPlayer(player, "Not in a group!")
        return
    end

    local members = group:GetMembers()
    if not members then
        DebugToPlayer(player, "Group members nil")
        return
    end

    -- DEBUG: сколько членов в группе
    DebugToPlayer(player, "Group members count: " .. tostring(#members))

    local targetBot = nil
    local targetName = "Unknown"

    for i = 1, #members do
        local member = members[i]
        if member then
            local mGuid = member:GetGUIDLow()
            local mName = member:GetName()
            DebugToPlayer(player, "Member #" .. i .. ": " .. mName .. " (guid=" .. mGuid .. ")")

            if mGuid ~= player:GetGUIDLow() then
                local lowerMsg = msg:lower()
                local lowerName = mName:lower()
                local addressed = (lowerMsg:find(lowerName, 1, true) ~= nil)
                    or (lowerMsg:find("бро", 1, true) ~= nil)
                    or (lowerMsg:find("пати", 1, true) ~= nil)
                    or (lowerMsg:find("все", 1, true) ~= nil)
                    or (lowerMsg:find("ребята", 1, true) ~= nil)

                if addressed then
                    targetBot = member
                    targetName = mName
                    DebugToPlayer(player, "SELECTED bot: " .. mName)
                    break
                end
            end
        end
    end

    if not targetBot then
        DebugToPlayer(player, "No bot addressed in party chat")
        return
    end

    local botGuid = targetBot:GetGUIDLow()

    if WriteRequestToDB(player, targetBot, msg, "PARTY", true) then
        DebugToPlayer(player, "Party request sent to AI...")
        local key = GenerateKey(player:GetGUIDLow())
        pendingChecks[key] = {
            playerGuid     = player:GetGUIDLow(),
            playerName     = player:GetName(),
            targetGuid     = botGuid,
            targetIsPlayer = true,
            targetName     = targetName,
            retries        = 0,
        }
    end
end

-- ============================================
-- HANDLE WHISPER
-- ============================================
local function HandleWhisperChannel(player, msg, targetNameInput)
    if not targetNameInput or targetNameInput == "" then
        DebugToPlayer(player, "Whisper target not found")
        return
    end

    local target = GetPlayerByName(targetNameInput)
    if not target then
        local allPlayers = GetPlayersInWorld()
        if allPlayers then
            for i = 1, #allPlayers do
                local p = allPlayers[i]
                if p and p:GetName():lower():find(targetNameInput:lower(), 1, true) then
                    target = p
                    break
                end
            end
        end
    end

    if not target then
        DebugToPlayer(player, "Bot '" .. targetNameInput .. "' not found")
        return
    end

    if target:GetGUIDLow() == player:GetGUIDLow() then
        DebugToPlayer(player, "Cannot whisper yourself")
        return
    end

    local tName = target:GetName()
    DebugToPlayer(player, "Whispering to: " .. tName)

    local targetGuid = target:GetGUIDLow()

    if WriteRequestToDB(player, target, msg, "WHISPER", true) then
        local key = GenerateKey(player:GetGUIDLow())
        pendingChecks[key] = {
            playerGuid     = player:GetGUIDLow(),
            playerName     = player:GetName(),
            targetGuid     = targetGuid,
            targetIsPlayer = true,
            targetName     = tName,
            retries        = 0,
        }
    end
end

-- ============================================
-- MAIN HANDLER
-- ============================================
-- ============================================
-- MAIN HANDLER (DIAGNOSTIC VERSION)
-- ============================================
local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    -- ЛОГ ВСЕГО, что ловит event 18
    Log(string.format("EVENT18: msgType=%d lang=%d target='%s' msg='%s'", 
        msgType, lang, tostring(targetName), msg))

    if not msg or #msg < 2 then return end
    if msg:sub(1, 1) == "." then return end

    -- Пока НЕ фильтруем по msgType — обрабатываем ВСЁ как SAY для теста
    -- чтобы понять, какой msgType приходит для PARTY
    if msgType == 1 then
        HandleSayChannel(player, msg)
    elseif msgType == 10 then
        HandlePartyChannel(player, msg)
    else
        -- ВРЕМЕННО: логируем неизвестные типы, но НЕ обрабатываем
        Log(string.format("UNHANDLED msgType=%d — add to handler if this is PARTY", msgType))
    end
end

-- ============================================
-- REGISTRATION
-- ============================================
RegisterPlayerEvent(18, OnPlayerChat)
Log("Living Azeroth AI Bridge [v2.5.1] loaded!")
Log("Channels: SAY | YELL | PARTY | WHISPER")
Log("FIX: No goto, nearest NPC, full diagnostics")

CreateLuaEvent(GlobalPollLoop, 500, 0)
Log("GlobalPollLoop started (500ms)")