if _G.LivingAzerothLoaded then
    print("[LivingAzeroth] Already loaded! Aborting second load.")
    return
end
_G.LivingAzerothLoaded = true

print("[LivingAzeroth] === FILE LOADING v3.2 ===")

-- ============================================
-- НАСТРОЙКИ (Settings)
-- ============================================
local AI_WORLD = {
    SEARCH_RADIUS = 30,
    FIND_RADIUS   = 100,
    BOT_PREFIX = "@",           -- Префикс команд ботам
    DEFAULT_TARGET = "all",     -- Если не удалось определить адресата — всем
    DEBUG = true,
}

-- ============================================
-- КОНСТАНТЫ ТИПОВ ЧАТА
-- ============================================
local CHAT_SAY           = 1
local CHAT_PARTY         = 2
local CHAT_RAID          = 3
local CHAT_GUILD         = 4
local CHAT_OFFICER       = 5
local CHAT_YELL          = 6
local CHAT_WHISPER       = 7
local CHAT_WHISPER_INFORM= 8
local CHAT_EMOTE         = 9
local CHAT_TEXT_EMOTE    = 10
local CHAT_SYSTEM        = 11
local CHAT_PARTY_LEADER  = 13
local CHAT_RAID_LEADER   = 14
local CHAT_RAID_WARNING  = 15

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
-- NPC LOOKUP BY GUID LOW
-- ============================================
local function FindCreatureByGUIDLow(player, guidLow)
    if not player then return nil end
    local creatures = player:GetCreaturesInRange(AI_WORLD.FIND_RADIUS)
    if not creatures then return nil end
    for i = 1, #creatures do
        local c = creatures[i]
        if c then
            local ok, low = pcall(function() return c:GetGUIDLow() end)
            if ok and low == guidLow then return c end
        end
    end
    local ok, result = pcall(function() return player:GetMap():GetCreature(guidLow) end)
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
    if not query then return false end
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
        if not sayOk then player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text) end
        if emoteId and emoteId > 0 then pcall(function() target:PerformEmote(emoteId) end) end
        DebugToPlayer(player, "NPC " .. targetName .. " replied via Say")
    elseif target and targetIsPlayer then
        local sayOk = pcall(function() target:Say(text, 0) end)
        if not sayOk then player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text) end
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
        local done = CheckAndDeliverResponse(data.playerGuid, data.playerName, data.targetGuid, data.targetIsPlayer, data.targetName)
        if done then
            pendingChecks[key] = nil
        else
            data.retries = data.retries + 1
            if data.retries > 60 then
                local p = FindPlayerByGUIDLow(data.playerGuid)
                if p then p:SendBroadcastMessage("|cffff0000[AI]|r Response timeout.") end
                pendingChecks[key] = nil
            end
        end
    end
end

-- ============================================
-- BOT TARGET PARSER (v3.2 — с fallback)
-- ============================================

local CLASS_ROLES = {
    [1] = "tank", [2] = "heal", [3] = "dps", [4] = "dps",
    [5] = "heal", [6] = "tank", [7] = "heal", [8] = "dps",
    [9] = "dps", [11] = "heal",
}

local function GetBotRole(bot)
    local ok, classId = pcall(function() return bot:GetClass() end)
    if ok and classId then return CLASS_ROLES[classId] or "dps" end
    return "dps"
end

-- Проверяет, является ли слово "общим" (не имя бота, не фильтр)
local function IsCommonWord(word)
    local common = {
        ["что"] = true, ["как"] = true, ["где"] = true, ["когда"] = true,
        ["почему"] = true, ["зачем"] = true, ["кто"] = true, ["сколько"] = true,
        ["да"] = true, ["нет"] = true, ["ок"] = true, ["го"] = true,
        ["привет"] = true, ["пока"] = true, ["спасибо"] = true,
        ["a"] = true, ["the"] = true, ["is"] = true, ["are"] = true,
        ["what"] = true, ["how"] = true, ["why"] = true, ["when"] = true,
        ["yes"] = true, ["no"] = true, ["ok"] = true, ["hi"] = true, ["hello"] = true,
    }
    return common[word:lower()] or false
end

local function ParseBotTargets(player, msg)
    local commandText = msg:sub(#AI_WORLD.BOT_PREFIX + 1)
    if not commandText or #commandText == 0 then
        return nil, "Empty command"
    end
    
    local firstWord = commandText:match("^(%S+)")
    if not firstWord then return nil, "Empty command" end
    
    local lowerFirst = firstWord:lower()
    local group = player:GetGroup()
    if not group then return nil, "Not in group" end
    
    local members = group:GetMembers()
    if not members then return nil, "No members" end
    
    local targets = {}
    local filterDesc = ""
    local isFallback = false  -- Был ли использован fallback на @all
    
    -- === РЕЖИМ 1: Явные групповые слова ===
    if lowerFirst == "all" or lowerFirst == "все" or lowerFirst == "пати" 
       or lowerFirst == "группа" or lowerFirst == "raid" or lowerFirst == "рейд" 
       or lowerFirst == "всем" or lowerFirst == "ребята" or lowerFirst == "бро" then
        
        filterDesc = "ALL"
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                table.insert(targets, member)
            end
        end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    -- === РЕЖИМ 2: Фильтр по роли ===
    elseif lowerFirst == "tank" or lowerFirst == "танк" or lowerFirst == "танки" then
        filterDesc = "ROLE:tank"
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                if GetBotRole(member) == "tank" then table.insert(targets, member) end
            end
        end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    elseif lowerFirst == "heal" or lowerFirst == "хил" or lowerFirst == "хилы" 
           or lowerFirst == "лекарь" or lowerFirst == "лекари" then
        filterDesc = "ROLE:heal"
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                if GetBotRole(member) == "heal" then table.insert(targets, member) end
            end
        end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    elseif lowerFirst == "dps" or lowerFirst == "дд" or lowerFirst == "дпс" then
        filterDesc = "ROLE:dps"
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                if GetBotRole(member) == "dps" then table.insert(targets, member) end
            end
        end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    elseif lowerFirst == "ranged" or lowerFirst == "рдд" or lowerFirst == "дальний" then
        filterDesc = "ROLE:ranged"
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                local ok, classId = pcall(function() return member:GetClass() end)
                if ok and classId and (classId == 3 or classId == 5 or classId == 7 or classId == 8 or classId == 9 or classId == 11) then
                    table.insert(targets, member)
                end
            end
        end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    elseif lowerFirst == "melee" or lowerFirst == "мдд" or lowerFirst == "ближний" then
        filterDesc = "ROLE:melee"
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                local ok, classId = pcall(function() return member:GetClass() end)
                if ok and classId and (classId == 1 or classId == 2 or classId == 4 or classId == 6) then
                    table.insert(targets, member)
                end
            end
        end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    -- === РЕЖИМ 3: Фильтр по классу ===
    elseif lowerFirst == "маг" or lowerFirst == "mage" then
        filterDesc = "CLASS:mage"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 8 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "жрец" or lowerFirst == "priest" or lowerFirst == "прист" then
        filterDesc = "CLASS:priest"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 5 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "воин" or lowerFirst == "warrior" then
        filterDesc = "CLASS:warrior"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 1 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "паладин" or lowerFirst == "paladin" or lowerFirst == "пал" then
        filterDesc = "CLASS:paladin"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 2 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "охотник" or lowerFirst == "hunter" or lowerFirst == "хант" then
        filterDesc = "CLASS:hunter"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 3 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "разбойник" or lowerFirst == "rogue" or lowerFirst == "рога" or lowerFirst == "разб" then
        filterDesc = "CLASS:rogue"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 4 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "дк" or lowerFirst == "deathknight" or lowerFirst == "рыцарь" then
        filterDesc = "CLASS:deathknight"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 6 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "шаман" or lowerFirst == "shaman" or lowerFirst == "шам" then
        filterDesc = "CLASS:shaman"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 7 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "чернокнижник" or lowerFirst == "warlock" or lowerFirst == "лок" or lowerFirst == "варлок" then
        filterDesc = "CLASS:warlock"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 9 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
    elseif lowerFirst == "друид" or lowerFirst == "druid" or lowerFirst == "дру" then
        filterDesc = "CLASS:druid"
        for i = 1, #members do local m = members[i]; if m and m:GetGUIDLow() ~= player:GetGUIDLow() then local ok, id = pcall(function() return m:GetClass() end); if ok and id == 11 then table.insert(targets, m) end end end
        commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        
    -- === РЕЖИМ 4: По имени бота (с fallback!) ===
    else
        -- Пытаемся найти бота по имени
        for i = 1, #members do
            local member = members[i]
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                local mName = member:GetName():lower()
                if mName:find(lowerFirst, 1, true) == 1 then
                    table.insert(targets, member)
                    break
                end
            end
        end
        
        if #targets > 0 then
            filterDesc = "NAME:" .. firstWord
            commandText = commandText:sub(#firstWord + 1):gsub("^%s+", "")
        else
            -- === FALLBACK: не нашли ни фильтр, ни имя — значит это "поболтать всем" ===
            -- Пример: "@а что еще скажешь?" → "а" не имя бота, не фильтр
            -- Значит вся строка после @ — это сообщение для всех ботов
            filterDesc = "ALL (fallback)"
            isFallback = true
            for i = 1, #members do
                local member = members[i]
                if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                    table.insert(targets, member)
                end
            end
            -- commandText остаётся как есть — вся строка после @
        end
    end
    
    return targets, filterDesc, commandText, isFallback
end

-- ============================================
-- HANDLE BOT COMMAND VIA SAY
-- ============================================
local function HandleBotCommandViaSay(player, msg)
    if msg:sub(1, #AI_WORLD.BOT_PREFIX) ~= AI_WORLD.BOT_PREFIX then
        return false
    end
    
    Log("BOT COMMAND: '" .. msg .. "'")
    
    local targets, filterDesc, commandText, isFallback = ParseBotTargets(player, msg)
    
    if not targets then
        DebugToPlayer(player, "Bot error: " .. tostring(filterDesc))
        return true
    end
    
    if #targets == 0 then
        DebugToPlayer(player, "No bots found for: " .. tostring(filterDesc))
        return true
    end
    
    DebugToPlayer(player, "Target[" .. filterDesc .. "] x" .. #targets .. ": " .. commandText)
    
    for _, bot in ipairs(targets) do
        local botName = bot:GetName()
        local botGuid = bot:GetGUIDLow()
        local fullCommand = "[" .. filterDesc .. "] " .. commandText
        
        if WriteRequestToDB(player, bot, fullCommand, "SAY-BOT", true) then
            DebugToPlayer(player, "→ " .. botName)
            local key = GenerateKey(player:GetGUIDLow())
            pendingChecks[key] = {
                playerGuid     = player:GetGUIDLow(),
                playerName     = player:GetGUIDLow(),
                targetGuid     = botGuid,
                targetIsPlayer = true,
                targetName     = botName,
                retries        = 0,
            }
        end
    end
    
    DebugToPlayer(player, "Sent to " .. #targets .. " bot(s)")
    return true
end

-- ============================================
-- HANDLE SAY (сначала боты, потом NPC)
-- ============================================
local function HandleSayChannel(player, msg)
    if HandleBotCommandViaSay(player, msg) then
        return
    end
    
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
                    if entry < 100000 then
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
local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    Log(string.format("=== EVENT18 === msgType=%d msg='%s'", msgType, msg))
    if not msg or #msg < 2 then return end
    if msg:sub(1, 1) == "." then return end

    if msgType == CHAT_SAY then
        HandleSayChannel(player, msg)
    elseif msgType == CHAT_WHISPER then
        HandleWhisperChannel(player, msg, targetName)
    elseif msgType == CHAT_PARTY or msgType == CHAT_PARTY_LEADER then
        Log("PARTY blocked by playerbots C++")
    else
        Log("msgType=" .. msgType .. " ignored")
    end
end

-- ============================================
-- REGISTRATION
-- ============================================
RegisterPlayerEvent(18, OnPlayerChat)
Log("Living Azeroth [v3.2] loaded!")
Log("Prefix: '" .. AI_WORLD.BOT_PREFIX .. "' | Fallback: ON")
Log("Usage: " .. AI_WORLD.BOT_PREFIX .. "all/tank/heal/dps/маг/Alfonso message")
Log("Fallback: any @" .. AI_WORLD.BOT_PREFIX .. "unknown → all bots")

CreateLuaEvent(GlobalPollLoop, 500, 0)
Log("GlobalPollLoop started (500ms)")