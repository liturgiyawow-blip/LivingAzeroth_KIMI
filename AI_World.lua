if _G.LivingAzerothLoaded then
    print("[LivingAzeroth] Already loaded! Aborting second load.")
    return
end
_G.LivingAzerothLoaded = true

print("[LivingAzeroth] === FILE LOADING v4.0 (Bots via SAY, NPC via @) ===")

-- ============================================
-- НАСТРОЙКИ
-- ============================================
local AI_WORLD = {
    SEARCH_RADIUS = 30,      -- Радиус поиска NPC
    BOT_RADIUS = 50,         -- Радиус поиска ботов
    NPC_PREFIX = "№",        -- Префикс для обращения к NPC
    DEBUG = true,
}

-- ============================================
-- КОНСТАНТЫ ТИПОВ ЧАТА
-- ============================================
local CHAT_SAY           = 1
local CHAT_PARTY         = 2
local CHAT_WHISPER       = 7

-- ============================================
-- КОНСТАНТЫ ДЛЯ ФИЛЬТРА NPC
-- ============================================
local CREATURE_TYPE_HUMANOID = 7

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
-- NPC FILTER
-- ============================================
local function IsRealNPC(creature)
    if not creature then return false end
    local okFlags, npcFlags = pcall(function() return creature:GetNPCFlags() end)
    if okFlags and npcFlags and npcFlags > 0 then return true end
    local okType, cType = pcall(function() return creature:GetCreatureType() end)
    local okClass, uClass = pcall(function() return creature:GetClass() end)
    if okType and cType == CREATURE_TYPE_HUMANOID and okClass and uClass and uClass > 0 then
        return true
    end
    return false
end

-- ============================================
-- PLAYER LOOKUP
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
-- NPC LOOKUP BY GUID
-- ============================================
local function FindCreatureByGUIDLow(player, guidLow)
    if not player then return nil end
    local creatures = player:GetCreaturesInRange(AI_WORLD.SEARCH_RADIUS)
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
-- WRITE REQUEST TO DB
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
-- DELIVER RESPONSE (боты шепчут, NPC говорят в SAY)
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
        -- NPC: говорит в SAY (облачко над головой)
        local sayOk = pcall(function() target:SendUnitSay(text, 0) end)
        if not sayOk then
            player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
        end
        if emoteId and emoteId > 0 then
            pcall(function() target:PerformEmote(emoteId) end)
        end
        DebugToPlayer(player, "NPC " .. targetName .. " replied via Say")

    elseif target and targetIsPlayer then
        -- БОТ: говорит в SAY (как раньше)
        local sayOk = pcall(function() target:Say(text, 0) end)
        if not sayOk then
            player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
        end
        DebugToPlayer(player, "Bot " .. targetName .. " replied via Say")

    else
        player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
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
-- BOT HANDLER: /s — только ОДИН случайный бот отвечает
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

local function HandleBotSay(player, msg)
    -- Ищем ботов в радиусе (не NPC!)
    local allPlayers = GetPlayersInWorld()
    if not allPlayers then return false end

    local bots = {}
    for i = 1, #allPlayers do
        local p = allPlayers[i]
        if p and p:GetGUIDLow() ~= player:GetGUIDLow() then
            -- Проверяем: это бот? (в одной группе и в радиусе)
            local okDist, dist = pcall(function() return player:GetDistance(p) end)
            if okDist and dist and dist < AI_WORLD.BOT_RADIUS then
                local group = player:GetGroup()
                if group then
                    local members = group:GetMembers()
                    if members then
                        for j = 1, #members do
                            if members[j] and members[j]:GetGUIDLow() == p:GetGUIDLow() then
                                table.insert(bots, p)
                                break
                            end
                        end
                    end
                end
            end
        end
    end

    if #bots == 0 then
        DebugToPlayer(player, "No bots nearby")
        return false
    end

    -- ВЫБИРАЕМ ОДНОГО СЛУЧАЙНОГО БОТА (не всех!)
    math.randomseed(os.time())
    local selectedBot = bots[math.random(1, #bots)]
    local botName = selectedBot:GetName()
    local botGuid = selectedBot:GetGUIDLow()

    DebugToPlayer(player, "Bot [" .. botName .. "] will reply (random from " .. #bots .. ")")

    if WriteRequestToDB(player, selectedBot, msg, "SAY-BOT", true) then
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

    return true
end

-- ============================================
-- NPC HANDLER: @имя — обращение к NPC
-- ============================================
local function HandleNPCSay(player, msg)
    -- Убираем префикс из начала
    local fullText = msg:sub(#AI_WORLD.NPC_PREFIX + 1):gsub("^%s+", "")
    if #fullText == 0 then
        DebugToPlayer(player, "Usage: " .. AI_WORLD.NPC_PREFIX .. "npc_name message")
        return true
    end

    -- Разделяем: первое слово = имя NPC, остальное = сообщение
    local npcNameInput, msgOnly = fullText:match("^(%S+)%s+(.*)$")
    
    -- Если нет пробела — всё это имя NPC, сообщение пустое
    if not npcNameInput then
        npcNameInput = fullText
        msgOnly = "привет"
    end
    
    -- Убираем восклицательный знак с конца имени (если пользователь поставил)
    npcNameInput = npcNameInput:gsub("!$", "")
    
    if #npcNameInput == 0 then
        DebugToPlayer(player, "Usage: " .. AI_WORLD.NPC_PREFIX .. "npc_name message")
        return true
    end

    -- Ищем NPC по имени в радиусе
    local creatures = player:GetCreaturesInRange(AI_WORLD.SEARCH_RADIUS)
    if not creatures then
        DebugToPlayer(player, "No creatures in range")
        return true
    end

    local targetNpc = nil
    local targetName = "Unknown"
    local bestMatch = 999

    local lowerInput = npcNameInput:lower()

    for i = 1, #creatures do
        local c = creatures[i]
        if c then
            local okAlive = pcall(function() return c:IsAlive() end)
            if okAlive and c:IsAlive() then
                if IsRealNPC(c) then
                    local okName, name = pcall(function() return c:GetName() end)
                    if okName and name then
                        local lowerName = name:lower()
                        
                        -- Проверяем: имя NPC начинается с введённого текста?
                        -- Или введённый текст начинается с имени NPC?
                        if lowerName:find(lowerInput, 1, true) == 1 or 
                           lowerInput:find(lowerName, 1, true) == 1 then
                            local okDist, dist = pcall(function() return player:GetDistance(c) end)
                            if okDist and dist and dist < bestMatch then
                                bestMatch = dist
                                targetNpc = c
                                targetName = name
                            end
                        end
                    end
                end
            end
        end
    end

    if not targetNpc then
        DebugToPlayer(player, "No NPC matching '" .. npcNameInput .. "' found")
        return true
    end

    -- Если сообщение пустое — заглушка
    if not msgOnly or #msgOnly == 0 then
        msgOnly = "привет"
    end

    local npcGuid = targetNpc:GetGUIDLow()
    local npcEntry = targetNpc:GetEntry()

    DebugToPlayer(player, "NPC: " .. targetName .. " | Msg: '" .. msgOnly .. "'")

    if WriteRequestToDB(player, targetNpc, msgOnly, "SAY-NPC", false) then
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

    return true
end

-- ============================================
-- MAIN HANDLER
-- ============================================
local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    Log(string.format("=== EVENT18 === msgType=%d msg='%s'", msgType, msg))
    if not msg or #msg < 2 then return end
    if msg:sub(1, 1) == "." then return end  -- Пропускаем команды сервера

    if msgType == CHAT_SAY then
        -- Разделение: @ = NPC, без @ = боты
        if msg:sub(1, #AI_WORLD.NPC_PREFIX) == AI_WORLD.NPC_PREFIX then
            -- Обращение к NPC через @
            HandleNPCSay(player, msg)
        else
            -- Обычный SAY — боты, один случайный отвечает
            HandleBotSay(player, msg)
        end

    elseif msgType == CHAT_WHISPER then
        -- WHISPER — только ботам (как раньше)
        local target = GetPlayerByName(targetName)
        if not target then
            local allPlayers = GetPlayersInWorld()
            if allPlayers then
                for i = 1, #allPlayers do
                    local p = allPlayers[i]
                    if p and p:GetName():lower():find(targetName:lower(), 1, true) then
                        target = p
                        break
                    end
                end
            end
        end
        if target and target:GetGUIDLow() ~= player:GetGUIDLow() then
            local tName = target:GetName()
            local tGuid = target:GetGUIDLow()
            if WriteRequestToDB(player, target, msg, "WHISPER", true) then
                local key = GenerateKey(player:GetGUIDLow())
                pendingChecks[key] = {
                    playerGuid     = player:GetGUIDLow(),
                    playerName     = player:GetName(),
                    targetGuid     = tGuid,
                    targetIsPlayer = true,
                    targetName     = tName,
                    retries        = 0,
                }
            end
        end

    elseif msgType == CHAT_PARTY or msgType == CHAT_PARTY_LEADER then
        -- PARTY — игнорируем (playerbots C++ обрабатывает)
        Log("PARTY chat ignored")
    end
end

-- ============================================
-- REGISTRATION
-- ============================================
RegisterPlayerEvent(18, OnPlayerChat)
Log("Living Azeroth [v4.0] loaded!")
Log("SAY -> random bot replies (whisper)")
Log("@" .. " -> NPC talks back (say with cloud)")
Log("WHISPER -> bots only")

CreateLuaEvent(GlobalPollLoop, 500, 0)
Log("GlobalPollLoop started (500ms)")