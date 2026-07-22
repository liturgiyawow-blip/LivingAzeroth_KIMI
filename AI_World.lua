if _G.LivingAzerothLoaded then
    print("[LivingAzeroth] Already loaded! Aborting second load.")
    return
end
_G.LivingAzerothLoaded = true

print("[LivingAzeroth] === FILE LOADING v5.3 ===")

-- ============================================
-- JSON БИБЛИОТЕКА
-- ============================================
local json = require("json")
require("CombatTriggers")

-- ============================================
-- НАСТРОЙКИ
-- ============================================
local AI_WORLD = {
    SEARCH_RADIUS = 30,
    FIND_RADIUS   = 100,
    NPC_PREFIX = "№",
    DEBUG = true,
    BOT_REPLY_TO_BOT_CHANCE = 5,
}

-- ============================================
-- КОНСТАНТЫ ТИПОВ ЧАТА
-- ============================================
local CHAT_SAY           = 1
local CHAT_PARTY         = 2
local CHAT_WHISPER       = 7
local CHAT_PARTY_LEADER = 4

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
-- DELIVER RESPONSE
-- ============================================
local function CheckAndDeliverResponse(playerGuid, playerName, targetGuid, targetIsPlayer, targetName)
    local player = FindPlayerByGUIDLow(playerGuid)
    if not player then
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
        if not sayOk then player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text) end
        if emoteId and emoteId > 0 then pcall(function() target:PerformEmote(emoteId) end) end
        DebugToPlayer(player, "NPC " .. targetName .. " replied via Say")
    elseif target and targetIsPlayer then
        local sayOk = pcall(function() target:Say(text, 0) end)
        if not sayOk then player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text) end
        DebugToPlayer(player, "Bot " .. targetName .. " replied via Say")
    else
        player:SendBroadcastMessage("|cff00ff00[" .. targetName .. "]:|r " .. text)
    end

    if actionCmd and actionCmd ~= "" and actionCmd ~= "null" then
        Log("Action command: " .. actionCmd)
    end
    
    Log(string.format("DELIVERED: %s -> %s: '%s'", targetName, playerName, text:sub(1, 50)))
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
-- BOT TARGET PARSER
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

-- ============================================
-- HANDLE SAY — БОТЫ
-- ============================================
local function HandleBotSay(player, msg)
    if msg:sub(1, #AI_WORLD.NPC_PREFIX) == AI_WORLD.NPC_PREFIX then
        return false
    end
    
    Log("BOT SAY: '" .. msg .. "'")
    
    local group = player:GetGroup()
    if not group then
        DebugToPlayer(player, "No group — bots won't respond")
        return false
    end
    
    local members = group:GetMembers()
    if not members then return false end
    
    local targets = {}
    for i = 1, #members do
        local member = members[i]
        if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
            local ok, isBot = pcall(function() return member:IsBot() end)
            if ok and isBot then
                table.insert(targets, member)
            end
        end
    end
    
    if #targets == 0 then
        DebugToPlayer(player, "No bots in group")
        return false
    end
    
    DebugToPlayer(player, "Broadcast to " .. #targets .. " bot(s): " .. msg)
    
    for _, bot in ipairs(targets) do
        local botName = bot:GetName()
        local botGuid = bot:GetGUIDLow()
        
        if WriteRequestToDB(player, bot, msg, "SAY-BOT", true) then
            DebugToPlayer(player, "-> " .. botName)
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
    
    return true
end

-- ============================================
-- HANDLE SAY — NPC
-- ============================================
local function HandleNPCSay(player, msg)
    if msg:sub(1, #AI_WORLD.NPC_PREFIX) ~= AI_WORLD.NPC_PREFIX then
        return false
    end
    
    Log("NPC COMMAND: '" .. msg .. "'")
    
    local afterPrefix = msg:sub(#AI_WORLD.NPC_PREFIX + 1)
    
    local npcNameInput = nil
    local msgOnly = afterPrefix
    
    local firstWord = afterPrefix:match("^(%S+)")
    if firstWord then
        local rest = afterPrefix:sub(#firstWord + 1):gsub("^%s+", "")
        if rest and #rest > 0 then
            npcNameInput = firstWord
            msgOnly = rest
        else
            msgOnly = firstWord
        end
    end
    
    local creatures = player:GetCreaturesInRange(AI_WORLD.SEARCH_RADIUS)
    if not creatures then
        DebugToPlayer(player, "No creatures in range")
        return true
    end
    
    local targetNpc = nil
    local targetName = "Unknown"
    local nearestDist = 9999
    
    local lowerSearchName = npcNameInput and npcNameInput:lower() or nil
    
    for i = 1, #creatures do
        local c = creatures[i]
        if c then
            local okAlive = pcall(function() return c:IsAlive() end)
            if okAlive and c:IsAlive() then
                if IsRealNPC(c) then
                    local okAttack, canAttack = pcall(function() return c:CanStartAttack(player, true) end)
                    if okAttack and canAttack then
                        goto continue_npc_loop
                    end
                    
                    local okDist, dist = pcall(function() return player:GetDistance(c) end)
                    local okName, cName = pcall(function() return c:GetName() end)
                    
                    if okDist and dist and okName and cName then
                        if lowerSearchName then
                            if cName:lower():find(lowerSearchName, 1, true) then
                                if dist < nearestDist then
                                    nearestDist = dist
                                    targetNpc = c
                                    targetName = cName
                                end
                            end
                        else
                            if dist < nearestDist then
                                nearestDist = dist
                                targetNpc = c
                                targetName = cName
                            end
                        end
                    end
                end
            end
        end
        ::continue_npc_loop::
    end
    
    if not targetNpc then
        if npcNameInput then
            DebugToPlayer(player, "No NPC matching '" .. npcNameInput .. "' found")
        else
            DebugToPlayer(player, "No NPC in range")
        end
        return true
    end
    
    if not msgOnly or #msgOnly == 0 then
        msgOnly = "привет"
    end
    
    local npcGuid = targetNpc:GetGUIDLow()
    local npcEntry = 0
    local okEntry, entryVal = pcall(function() return targetNpc:GetEntry() end)
    if okEntry then npcEntry = entryVal end
    
    if WriteRequestToDB(player, targetNpc, msgOnly, "SAY", false) then
        DebugToPlayer(player, "Talking to NPC: " .. targetName .. " (entry=" .. npcEntry .. ")")
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
local botReplyDepth = {}

local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    Log(string.format("=== EVENT18 === msgType=%d msg='%s'", msgType, msg))
    if not msg or #msg < 1 then return end
    if msg:sub(1, 1) == "." then return end

    local okIsBot, isBot = pcall(function() return player:IsBot() end)
    if okIsBot and isBot then
        if AI_WORLD.BOT_REPLY_TO_BOT_CHANCE <= 0 then
            return
        end
        
        local botGuid = player:GetGUIDLow()
        local depth = botReplyDepth[botGuid] or 0
        if depth >= 2 then
            return
        end
        
        if math.random(1, 100) > AI_WORLD.BOT_REPLY_TO_BOT_CHANCE then
            return
        end
        
        botReplyDepth[botGuid] = depth + 1
    else
        botReplyDepth = {}
    end

    if msgType == CHAT_SAY then
        if HandleNPCSay(player, msg) then
            return
        end
        HandleBotSay(player, msg)
    elseif msgType == CHAT_WHISPER then
        HandleWhisperChannel(player, msg, targetName)
    elseif msgType == CHAT_PARTY or msgType == CHAT_PARTY_LEADER then
        Log("PARTY chat ignored (handled by playerbots C++)")
    else
        Log("msgType=" .. msgType .. " ignored")
    end
end

local combatSessions = {}

-- ============================================
-- COMBAT: WEAPON DETECTION v5.3-fix2
-- ============================================

local function GetWeaponName(unit)
    local unitName = "Unknown"
    pcall(function() unitName = unit:GetName() end)
    
    -- В разных сборках AzerothCore слоты отличаются.
    -- Проверяем 15, 16, 17 — первый найденный = оружие.
    local methods = {
        { name = "Slot15", fn = function() return unit:GetEquippedItemBySlot(15) end },
        { name = "Slot16", fn = function() return unit:GetEquippedItemBySlot(16) end },
        { name = "Slot17", fn = function() return unit:GetEquippedItemBySlot(17) end },
    }
    
    for _, m in ipairs(methods) do
        local ok, item = pcall(m.fn)
        if ok and item then
            local ok2, itemName = pcall(function() return item:GetName() end)
            if ok2 and itemName and itemName ~= "" then
                Log(string.format("[WeaponDebug] %s -> %s via %s", unitName, itemName, m.name))
                return itemName
            end
        end
    end
    
    Log(string.format("[WeaponDebug] %s -> FAILED (no weapon)", unitName))
    return "руки"
end


-- ============================================
-- COMBAT: ENTER COMBAT v5.3-fix2
-- ============================================

local function OnEnterCombat(event, player, enemy)
    local okIsBot, isBot = pcall(function() return player:IsBot() end)
    if okIsBot and isBot then
        return
    end
    
    local guid = player:GetGUIDLow()
    local group = player:GetGroup()
    if not group then
        return
    end
    
    -- ═══════════════════════════════════════════════════════════════
    -- FIX v5.3-fix2: Тройной fallback для имени врага
    -- ═══════════════════════════════════════════════════════════════
    local enemyName = "неизвестный враг"
    
    -- Попытка 1: аргумент события
    if enemy then
        local ok, name = pcall(function() return enemy:GetName() end)
        if ok and name and name ~= "" then
            enemyName = name
        end
    end
    
    -- Попытка 2: текущая цель игрока (victim)
    if enemyName == "неизвестный враг" then
        local ok, victim = pcall(function() return player:GetVictim() end)
        if ok and victim then
            local ok2, name = pcall(function() return victim:GetName() end)
            if ok2 and name and name ~= "" then
                enemyName = name
                Log("[CombatDebug] Enemy resolved from GetVictim: " .. enemyName)
            end
        end
    end
    
    -- Попытка 3: выделенная цель (selection)
    if enemyName == "неизвестный враг" then
        local ok, selection = pcall(function() return player:GetSelection() end)
        if ok and selection then
            local ok2, name = pcall(function() return selection:GetName() end)
            if ok2 and name and name ~= "" then
                enemyName = name
                Log("[CombatDebug] Enemy resolved from GetSelection: " .. enemyName)
            end
        end
    end
    
    Log(string.format("[CombatDebug] EnterCombat: final enemyName=%s", enemyName))
    
    local participants = {}
    
    -- Игрок (лидер)
    local okHp, hpStart = pcall(function() return player:GetHealthPct() end)
    local okMaxHp, maxHp = pcall(function() return player:GetMaxHealth() end)
    local okMana, manaStart = pcall(function() return player:GetPowerPct(0) end)
    local okClass, className = pcall(function() return player:GetClassAsString() end)
    local okRace, raceName = pcall(function() return player:GetRaceAsString() end)
    
    table.insert(participants, {
        guid = player:GetGUIDLow(),
        name = player:GetName(),
        class = okClass and className or "Unknown",
        race = okRace and raceName or "Unknown",
        hp_start = okHp and hpStart or 100,
        max_hp = okMaxHp and maxHp or 1000,
        mana_start = okMana and manaStart or 100,
        deaths = 0,
        is_player = true,
        main_hand = GetWeaponName(player),
    })
    
    -- Боты
    local members = group:GetMembers()
    for _, member in ipairs(members) do
        if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
            local ok, isBotMember = pcall(function() return member:IsBot() end)
            if ok and isBotMember then
                local okHp2, hpStart2 = pcall(function() return member:GetHealthPct() end)
                local okMaxHp2, maxHp2 = pcall(function() return member:GetMaxHealth() end)
                local okMana2, manaStart2 = pcall(function() return member:GetPowerPct(0) end)
                local okClass2, className2 = pcall(function() return member:GetClassAsString() end)
                local okRace2, raceName2 = pcall(function() return member:GetRaceAsString() end)
                
                table.insert(participants, {
                    guid = member:GetGUIDLow(),
                    name = member:GetName(),
                    class = okClass2 and className2 or "Unknown",
                    race = okRace2 and raceName2 or "Unknown",
                    hp_start = okHp2 and hpStart2 or 100,
                    max_hp = okMaxHp2 and maxHp2 or 1000,
                    mana_start = okMana2 and manaStart2 or 100,
                    deaths = 0,
                    is_player = false,
                    main_hand = GetWeaponName(member),
                })
            end
        end
    end
    
    if #participants <= 1 then
        Log("OnEnterCombat: only player, no bots in group")
        return
    end
    
    if combatSessions[guid] then
        Log("Combat session restarted for " .. player:GetName())
    end

    combatSessions[guid] = {
        active = true,
        start_time = os.time(),
        leader_guid = guid,
        leader_name = player:GetName(),
        participants = participants,
        enemies = { enemyName },
        total_deaths = 0,
        boss_killed = false,
    }    
    Log("Combat session started for " .. player:GetName() .. " with " .. (#participants - 1) .. " bots (total " .. #participants .. " participants)")
end


local function OnKillCreature(event, player, killed)
    local guid = player:GetGUIDLow()
    local session = combatSessions[guid]
    if not session or not session.active then return end
    
    local ok, enemyName = pcall(function() return killed:GetName() end)
    if not ok or not enemyName then enemyName = "неизвестный враг" end
    table.insert(session.enemies, enemyName)
    
    local okRank, rank = pcall(function() return killed:GetRank() end)
    local okHp, maxHp = pcall(function() return killed:GetMaxHealth() end)
    if (okRank and rank >= COMBAT_CONFIG.THRESHOLD_BOSS_RANK) or (okHp and maxHp > COMBAT_CONFIG.THRESHOLD_BOSS_HP) then
        session.boss_killed = true
        session.boss_name = enemyName
    end
end

-- ============================================
-- COMBAT: LEAVE COMBAT v5.3-fix
-- ============================================

local function OnLeaveCombat(event, player)
    local okIsBot, isBot = pcall(function() return player:IsBot() end)
    if okIsBot and isBot then
        return
    end
    
    local guid = player:GetGUIDLow()
    local session = combatSessions[guid]
    
    if not session then
        return
    end
    
    if not session.active then
        return
    end
    
    session.active = false
    session.end_time = os.time()
    session.duration = session.end_time - session.start_time

    -- Логируем raw enemies
    Log(string.format("[CombatDebug] LeaveCombat: raw enemies count=%d", #(session.enemies or {})))
    for i, name in ipairs(session.enemies or {}) do
        Log(string.format("[CombatDebug]   raw[%d] = %s (type=%s)", i, tostring(name), type(name)))
    end
    
    -- Дедупликация
    local uniqueEnemies = {}
    local enemySeen = {}
    for _, name in ipairs(session.enemies or {}) do
        if name and name ~= "" and name ~= "неизвестный враг" then
            if not enemySeen[name] then
                enemySeen[name] = true
                table.insert(uniqueEnemies, name)
            end
        end
    end
    
    if #uniqueEnemies == 0 and #(session.enemies or {}) > 0 then
        for _, name in ipairs(session.enemies) do
            if name and name ~= "" then
                table.insert(uniqueEnemies, name)
                Log(string.format("[CombatDebug] Fallback enemy used: %s", tostring(name)))
                break
            end
        end
    end
    
    if #uniqueEnemies > 5 then
        local trimmed = {}
        for i = 1, 5 do table.insert(trimmed, uniqueEnemies[i]) end
        uniqueEnemies = trimmed
    end
    
    Log(string.format("[CombatDebug] uniqueEnemies final count=%d", #uniqueEnemies))
    for i, name in ipairs(uniqueEnemies) do
        Log(string.format("[CombatDebug]   unique[%d] = %s", i, tostring(name)))
    end
    
    -- FIX: защита от nil для всех участников
    for _, p in ipairs(session.participants or {}) do
        local member = FindPlayerByGUIDLow(p.guid)
        if member then
            local okHp, hpEnd = pcall(function() return member:GetHealthPct() end)
            local okMana, manaEnd = pcall(function() return member:GetPowerPct(0) end)
            p.hp_end = (okHp and hpEnd) or 0
            p.mana_end = (okMana and manaEnd) or 0
        else
            p.hp_end = p.hp_end or 0
            p.mana_end = p.mana_end or 0
        end
    end
    
    Log("=== COMBAT PARTICIPANTS FINAL STATE ===")
    for _, p in ipairs(session.participants or {}) do
        local hpLost = (p.hp_start or 100) - (p.hp_end or 0)
        local manaLost = (p.mana_start or 100) - (p.mana_end or 0)
        -- FIX: защита string.format от nil
        Log(string.format("  %s (%s): hp=%.0f->%.0f (lost %.0f%%), mana=%.0f->%.0f (lost %.0f%%), deaths=%.0f, is_player=%s",
            tostring(p.name), tostring(p.class), 
            (p.hp_start or 0), (p.hp_end or 0), hpLost,
            (p.mana_start or 0), (p.mana_end or 0), manaLost,
            (p.deaths or 0), tostring(p.is_player)))
    end
    
    local severity, modifiers, triggers = EvaluateTriggers(session, session.participants)
    
    local finalChance = math.min(COMBAT_CONFIG.MAX_CHANCE, COMBAT_CONFIG.BASE_CHANCE_BOT + severity)
    
    -- Выбираем говорящего только среди БОТОВ
    local speaker = nil
    local aliveBots = {}
    for _, p in ipairs(session.participants or {}) do
        if not p.is_player and (p.deaths or 0) == 0 then
            table.insert(aliveBots, p)
        end
    end
    
    if #aliveBots > 0 then
        speaker = aliveBots[math.random(1, #aliveBots)]
    end
    
    if not speaker then
        Log("No bot survivors to comment on combat")
        combatSessions[guid] = nil
        return
    end
    
    -- FIX: защита speaker
    if not speaker.guid or not speaker.name then
        Log("ERROR: selected speaker is invalid")
        combatSessions[guid] = nil
        return
    end
    
    local roll = math.random(1, 100)
    Log("Combat ended. Chance: " .. finalChance .. "%, rolled: " .. roll .. " (severity=" .. severity .. ")")
    
    if roll > finalChance then
        Log("No post-combat phrase (roll failed)")
        combatSessions[guid] = nil
        return
    end
    
    -- FIX: leader_main_hand из первого участника (игрок)
    local leaderMainHand = "руки"
    if session.participants and session.participants[1] and session.participants[1].main_hand then
        leaderMainHand = session.participants[1].main_hand
    end
    
    local rpData = {
        leader_guid = session.leader_guid,
        leader_name = session.leader_name,
        leader_main_hand = leaderMainHand,
        speaker_guid = speaker.guid,
        speaker_name = speaker.name,
        speaker_class = speaker.class or "Unknown",
        speaker_race = speaker.race or "Unknown",
        duration_desc = DescribeDuration(session.duration),
        duration_sec = session.duration,
        severity = severity,
        modifiers = modifiers,
        triggers = triggers,
        casualties = {},
        wounded = {},
        heroes = {},
        participants = {},
        enemies_names = uniqueEnemies,
        boss_name = session.boss_name or nil,
        enemy_count = #(session.enemies or {}),
        speaker_main_hand = speaker.main_hand or "руки",
    }

    for _, p in ipairs(session.participants or {}) do
        table.insert(rpData.participants, tostring(p.name))
    end
        
    for _, p in ipairs(session.participants or {}) do
        if (p.deaths or 0) > 0 then
            table.insert(rpData.casualties, tostring(p.name))
        else
            local lost = (p.hp_start or 100) - (p.hp_end or 0)
            if lost >= COMBAT_CONFIG.THRESHOLD_CRITICAL_HP_LOST then
                table.insert(rpData.wounded, { name = tostring(p.name), state = "на грани смерти" })
            elseif lost >= COMBAT_CONFIG.THRESHOLD_WOUNDED_HP_LOST then
                table.insert(rpData.wounded, { name = tostring(p.name), state = "серьёзно ранен" })
            end
            
            if lost >= COMBAT_CONFIG.THRESHOLD_HERO_HP_LOST and (#rpData.casualties > 0 or session.duration > COMBAT_CONFIG.THRESHOLD_LONG_FIGHT_SEC) then
                table.insert(rpData.heroes, tostring(p.name))
            end
        end
    end
    
    local jsonData = json.encode(rpData)
    
    Log("[CombatDebug] JSON enemies_names in payload: " .. tostring(#uniqueEnemies))
    Log("[CombatDebug] JSON first 800 chars: " .. tostring(jsonData):sub(1, 800))
    
    local sql = string.format(
        "INSERT INTO ai_requests " ..
        "(player_guid, player_name, npc_guid, npc_entry, npc_name, message, channel_type, target_is_player, created_at) " ..
        "VALUES (%u, '%s', %u, %d, '%s', '%s', '%s', %d, UNIX_TIMESTAMP())",
        tonumber(session.leader_guid) or 0,
        EscapeSQL(session.leader_name),
        tonumber(speaker.guid) or 0,
        0,
        EscapeSQL(speaker.name),
        EscapeSQL(jsonData),
        "POST-COMBAT",
        1
    )
    
    local status, err = pcall(function() CharDBExecute(sql) end)
    if status then
        Log("Post-combat phrase queued for " .. tostring(speaker.name) .. " (leader=" .. tostring(session.leader_name) .. ")")
        
        local key = GenerateKey(session.leader_guid)
        pendingChecks[key] = {
            playerGuid     = session.leader_guid,
            playerName     = session.leader_name,
            targetGuid     = speaker.guid,
            targetIsPlayer = true,
            targetName     = speaker.name,
            retries        = 0,
        }
    else
        Log("SQL ERROR (post-combat): " .. tostring(err))
    end
    
    combatSessions[guid] = nil
end

-- ============================================
-- REGISTRATION
-- ============================================
RegisterPlayerEvent(18, OnPlayerChat)
RegisterPlayerEvent(33, OnEnterCombat)
RegisterPlayerEvent(34, OnLeaveCombat)
RegisterPlayerEvent(7, OnKillCreature)

Log("Living Azeroth [v5.3] loaded!")
Log("NPC prefix: '" .. AI_WORLD.NPC_PREFIX .. "'")
Log("CombatAnalyst: modular triggers, MAX_CHANCE=" .. COMBAT_CONFIG.MAX_CHANCE .. "%")

CreateLuaEvent(GlobalPollLoop, 1000, 0)
Log("GlobalPollLoop started (1000ms)")