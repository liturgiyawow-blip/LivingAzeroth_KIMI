if _G.LivingAzerothLoaded then
    print("[LivingAzeroth] Already loaded! Aborting second load.")
    return
end
_G.LivingAzerothLoaded = true

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
-- HANDLE PARTY v2.0 — с фильтрами @роль
-- ============================================

-- Таблица: ID класса → роль (упрощённо)
local CLASS_ROLES = {
    [1]  = "tank",    -- Warrior
    [2]  = "heal",    -- Paladin (может быть и танком, но упрощаем)
    [3]  = "dps",     -- Hunter
    [4]  = "dps",     -- Rogue
    [5]  = "heal",    -- Priest
    [6]  = "tank",    -- Death Knight
    [7]  = "heal",    -- Shaman
    [8]  = "dps",     -- Mage
    [9]  = "dps",     -- Warlock
    [11] = "heal",    -- Druid
}

local function GetBotRole(bot)
    --Определяем роль бота по классу--
    local ok, classId = pcall(function() return bot:GetClass() end)
    if ok and classId then
        return CLASS_ROLES[classId] or "dps"
    end
    return "dps"
end

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

    local lowerMsg = msg:lower()

    -- ========================================
    -- ШАГ 1: Проверяем, есть ли фильтр @роль
    -- ========================================
    
    local roleFilter = nil
    
    -- Проверяем фильтры @tank, @heal, @dps и т.д.
    if lowerMsg:find("@tank", 1, true) then
        roleFilter = "tank"
    elseif lowerMsg:find("@heal", 1, true) or lowerMsg:find("@хил", 1, true) then
        roleFilter = "heal"
    elseif lowerMsg:find("@dps", 1, true) or lowerMsg:find("@дд", 1, true) then
        roleFilter = "dps"
    elseif lowerMsg:find("@ranged", 1, true) or lowerMsg:find("@рдд", 1, true) then
        roleFilter = "ranged"
    elseif lowerMsg:find("@melee", 1, true) or lowerMsg:find("@мдд", 1, true) then
        roleFilter = "melee"
    end

    -- ========================================
    -- ШАГ 2: Собираем цели
    -- ========================================
    
    local targets = {}  -- список {bot = bot, name = name}
    
    for i = 1, #members do
        local member = members[i]
        if member then
            local mGuid = member:GetGUIDLow()
            local mName = member:GetName()
            
            -- Пропускаем самого игрока
            if mGuid ~= player:GetGUIDLow() then
                local lowerName = mName:lower()
                
                -- Режим 1: Фильтр по роли
                if roleFilter then
                    local botRole = GetBotRole(member)
                    if botRole == roleFilter then
                        table.insert(targets, {bot = member, name = mName})
                        DebugToPlayer(player, "FILTER @" .. roleFilter .. " -> " .. mName)
                    end
                    
                -- Режим 2: Обращение по имени или общие слова
                else
                    local addressed = (lowerMsg:find(lowerName, 1, true) ~= nil)
                        or (lowerMsg:find("бро", 1, true) ~= nil)
                        or (lowerMsg:find("пати", 1, true) ~= nil)
                        or (lowerMsg:find("все", 1, true) ~= nil)
                        or (lowerMsg:find("ребята", 1, true) ~= nil)
                        or (lowerMsg:find("групп", 1, true) ~= nil)

                    if addressed then
                        table.insert(targets, {bot = member, name = mName})
                        DebugToPlayer(player, "SELECTED bot: " .. mName)
                        break  -- В режиме имени берём только первого
                    end
                end
            end
        end
    end

    -- ========================================
    -- ШАГ 3: Отправляем запросы
    -- ========================================
    
    if #targets == 0 then
        if roleFilter then
            DebugToPlayer(player, "No bots found for filter: @" .. roleFilter)
        else
            DebugToPlayer(player, "No bot addressed in party chat")
        end
        return
    end

    -- Отправляем запрос каждому целевому боту
    for _, target in ipairs(targets) do
        local bot = target.bot
        local botName = target.name
        local botGuid = bot:GetGUIDLow()

        if WriteRequestToDB(player, bot, msg, "PARTY", true) then
            DebugToPlayer(player, "Party request sent to: " .. botName)
            local key = GenerateKey(player:GetGUIDLow())
            pendingChecks[key] = {
                playerGuid     = player:GetGUIDLow(),
                playerName     = player:GetName(),
                targetGuid     = botGuid,
                targetIsPlayer = true,
                targetName     = botName,
                retries        = 0,
            }
        end
    end
    
    -- Если было несколько целей (фильтр @роль), покажем summary
    if #targets > 1 then
        DebugToPlayer(player, "Sent to " .. #targets .. " bot(s)")
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
-- MAIN HANDLER (ДИАГНОСТИКА ВСЕХ ТИПОВ ЧАТА)
-- ============================================
local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    -- ЛОГ ВСЕГО подряд — чтобы увидеть, какой msgType приходит
    Log(string.format("EVENT18: msgType=%d lang=%d target='%s' msg='%s'", 
        msgType, lang, tostring(targetName), msg))

    if not msg or #msg < 2 then return end
    if msg:sub(1, 1) == "." then return end

    -- Обрабатываем ВСЕ известные типы, а неизвестные логируем отдельно
    
    -- SAY = 1
    if msgType == 1 then
        HandleSayChannel(player, msg)
        
    -- PARTY = 10 (стандарт WoW)
    elseif msgType == 10 then
        HandlePartyChannel(player, msg)
        
    -- WHISPER = 7 (стандарт WoW)
    elseif msgType == 7 then
        HandleWhisperChannel(player, msg, targetName)
        
    -- RAID = 11 (стандарт WoW)
    elseif msgType == 11 then
        Log("RAID chat detected! msgType=11")
        HandlePartyChannel(player, msg)  -- Обрабатываем как пати
        
    -- GUILD = 9
    elseif msgType == 9 then
        Log("GUILD chat detected! msgType=9")
        
    -- YELL = 6
    elseif msgType == 6 then
        Log("YELL chat detected! msgType=6")
        
    -- EMOTE = 3
    elseif msgType == 3 then
        Log("EMOTE detected! msgType=3")
        
    -- SYSTEM = 2
    elseif msgType == 2 then
        Log("SYSTEM msg detected! msgType=2")
        
    -- Всё остальное — логируем, чтобы узнать
    else
        Log(string.format("UNKNOWN msgType=%d — нужно добавить обработчик! msg='%s'", msgType, msg))
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