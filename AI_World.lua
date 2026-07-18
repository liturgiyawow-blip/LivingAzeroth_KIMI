if _G.LivingAzerothLoaded then
    print("[LivingAzeroth] Already loaded! Aborting second load.")
    return
end
_G.LivingAzerothLoaded = true

print("[LivingAzeroth] === FILE LOADING v5.0 ===")

-- ============================================
-- JSON БИБЛИОТЕКА
-- ============================================
local json = require("json")

-- ============================================
-- НАСТРОЙКИ
-- ============================================
local AI_WORLD = {
    SEARCH_RADIUS = 30,
    FIND_RADIUS   = 100,
    NPC_PREFIX = "№",
    DEBUG = true,
    -- FIX: Шанс ответа боту на сообщение бота (0 = отключено, 10 = 10%)
    BOT_REPLY_TO_BOT_CHANCE = 5,
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
-- HANDLE SAY — БОТЫ (любой текст в /s = боты слышат)
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
-- HANDLE SAY — NPC (№префикс)
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

-- FIX: Счётчик ответов ботов на ботов (защита от цепной реакции)
local botReplyDepth = {}

local function OnPlayerChat(event, player, msg, msgType, lang, targetName)
    Log(string.format("=== EVENT18 === msgType=%d msg='%s'", msgType, msg))
    if not msg or #msg < 1 then return end
    if msg:sub(1, 1) == "." then return end

    -- FIX: Проверяем, говорит ли бот
    local okIsBot, isBot = pcall(function() return player:IsBot() end)
    if okIsBot and isBot then
        -- Это бот говорит
        if AI_WORLD.BOT_REPLY_TO_BOT_CHANCE <= 0 then
            -- 0% — полностью игнорируем сообщения ботов
            Log("Bot speech ignored (BOT_REPLY_TO_BOT_CHANCE = 0)")
            return
        end
        
        -- Проверяем глубину цепочки
        local botGuid = player:GetGUIDLow()
        local depth = botReplyDepth[botGuid] or 0
        if depth >= 2 then
            Log("Bot reply depth limit reached for " .. botGuid)
            return
        end
        
        -- Шанс ответить
        if math.random(1, 100) > AI_WORLD.BOT_REPLY_TO_BOT_CHANCE then
            Log("Bot speech ignored (chance roll failed)")
            return
        end
        
        -- Разрешаем ответ, увеличиваем глубину
        botReplyDepth[botGuid] = depth + 1
        Log("Bot speech ALLOWED (depth=" .. (depth + 1) .. ", chance=" .. AI_WORLD.BOT_REPLY_TO_BOT_CHANCE .. "%)")
    else
        -- Живой игрок — сбрасываем счётчики глубины
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

-- ============================================
-- COMBAT ANALYST MODULE v1.0 — РП-версия
-- ============================================

local COMBAT_CONFIG = {
    -- Шанс базовый (живой игрок говорит после боя)
    BASE_CHANCE_PLAYER = 0,
    
    -- Шанс базовый (бот комментирует после боя)
    BASE_CHANCE_BOT = 15,
    
    -- Модификаторы тяжести (складываются к BASE_CHANCE_BOT)
    MOD_EASY_FIGHT = 0,
    MOD_WOUNDED = 15,
    MOD_CRITICALLY_WOUNDED = 30,
    MOD_DEATH = 50,
    MOD_BOSS_KILL = 20,
    MOD_LONG_FIGHT = 10,
    MOD_SOLO_SURVIVOR = 60,
    
    -- Максимальный шанс
    MAX_CHANCE = 85,
    
    -- Задержка перед фразой (секунды)
    --DELAY_MIN = 0,
    --DELAY_MAX = 1,
}

-- Хранилище боёв: [leader_guid] = session_data
local combatSessions = {}

-- РП-описания состояния ранений
local WOUND_DESCRIPTIONS = {
    [0] = "без царапины",
    [1] = "лёгкие царапины",
    [2] = "серьёзные раны",
    [3] = "на грани смерти",
    [4] = "пал в бою",
}

-- РП-описания длительности боя
local DURATION_DESCRIPTIONS = {
    short = "краткая схватка",
    medium = "ожесточённый бой",
    long = "долгая, изнурительная резня",
    epic = "битва, о которой будут слагать легенды",
}

-- Описать состояние ранений персонажа (РП, без процентов)
local function DescribeWoundState(hpStart, hpEnd, deaths)
    if deaths > 0 then return WOUND_DESCRIPTIONS[4] end
    local lost = hpStart - hpEnd
    if lost < 10 then return WOUND_DESCRIPTIONS[0]
    elseif lost < 30 then return WOUND_DESCRIPTIONS[1]
    elseif lost < 60 then return WOUND_DESCRIPTIONS[2]
    else return WOUND_DESCRIPTIONS[3] end
end

-- Описать длительность боя (РП)
local function DescribeDuration(seconds)
    if seconds < 30 then return DURATION_DESCRIPTIONS.short
    elseif seconds < 120 then return DURATION_DESCRIPTIONS.medium
    elseif seconds < 300 then return DURATION_DESCRIPTIONS.long
    else return DURATION_DESCRIPTIONS.epic end
end

-- ============================================
-- COMBAT EVENT HANDLERS
-- ============================================

-- Игрок вошёл в бой
-- Игрок вошёл в бой
local function OnEnterCombat(event, player, enemy)
    -- FIX v5.1: Только живой игрок триггерит CombatAnalyst
    -- Боты в группе уже учтены как участники
    local okIsBot, isBot = pcall(function() return player:IsBot() end)
    if okIsBot and isBot then
        return  -- Игнорируем combat ботов
    end
    
    local guid = player:GetGUIDLow()
    local group = player:GetGroup()
    if not group then return end
    
    -- Собираем ТОЛЬКО ботов в группе
    local members = group:GetMembers()
    local participants = {}
    
    for _, member in ipairs(members) do
        local ok, isBotMember = pcall(function() return member:IsBot() end)
        if ok and isBotMember then
            table.insert(participants, {
                guid = member:GetGUIDLow(),
                name = member:GetName(),
                class = member:GetClassAsString(),
                race = member:GetRaceAsString(),
                hp_start = member:GetHealthPct(),
                max_hp = member:GetMaxHealth(),
                deaths = 0,
            })
        end
    end
    
    if #participants == 0 then return end
    
    combatSessions[guid] = {
        active = true,
        start_time = os.time(),
        leader_guid = guid,
        leader_name = player:GetName(),
        participants = participants,
        enemies = {},
        total_deaths = 0,
        boss_killed = false,
    }
    
    Log("Combat session started for " .. player:GetName() .. " with " .. #participants .. " bots")
end
-- Игрок убил существо
local function OnKillCreature(event, player, killed)
    local guid = player:GetGUIDLow()
    local session = combatSessions[guid]
    if not session or not session.active then return end
    
    local enemyName = killed:GetName()
    table.insert(session.enemies, enemyName)
    
    -- Проверяем, босс ли это (по rank или здоровью)
    local ok, rank = pcall(function() return killed:GetRank() end)
    local ok2, maxHp = pcall(function() return killed:GetMaxHealth() end)
    if (ok and rank >= 2) or (ok2 and maxHp > 100000) then
        session.boss_killed = true
        session.boss_name = enemyName
    end
end

-- Игрок вышел из боя
local function OnLeaveCombat(event, player)
    local guid = player:GetGUIDLow()
    local session = combatSessions[guid]
    if not session or not session.active then return end
    
    session.active = false
    session.end_time = os.time()
    session.duration = session.end_time - session.start_time
    
    -- Собираем финальные данные
    for _, p in ipairs(session.participants) do
        local member = FindPlayerByGUIDLow(p.guid)
        if member then
            p.hp_end = member:GetHealthPct()
            if not p.hp_end then p.hp_end = 0 end
        else
            -- Бот не в мире — считаем мёртвым
            p.hp_end = 0
            p.deaths = 1
        end
    end
    
    -- Анализируем тяжесть боя
    local severity = 0
    local modifiers = {}
    
    local hasWounded = false
    local hasCriticallyWounded = false
    local hasDeath = false
    
    for _, p in ipairs(session.participants) do
        if p.deaths > 0 then
            hasDeath = true
        else
            local lost = (p.hp_start or 100) - (p.hp_end or 0)
            if lost >= 60 then hasCriticallyWounded = true
            elseif lost >= 30 then hasWounded = true end
        end
    end
    
    if hasDeath then 
        severity = severity + COMBAT_CONFIG.MOD_DEATH
        table.insert(modifiers, "потери")
    elseif hasCriticallyWounded then
        severity = severity + COMBAT_CONFIG.MOD_CRITICALLY_WOUNDED
        table.insert(modifiers, "тяжёлые ранения")
    elseif hasWounded then
        severity = severity + COMBAT_CONFIG.MOD_WOUNDED
        table.insert(modifiers, "ранения")
    else
        severity = severity + COMBAT_CONFIG.MOD_EASY_FIGHT
        table.insert(modifiers, "лёгкий бой")
    end
    
    -- Длительность
    if session.duration > 180 then
        severity = severity + COMBAT_CONFIG.MOD_LONG_FIGHT
        table.insert(modifiers, "долгий бой")
    end
    
    -- Босс
    if session.boss_killed then
        severity = severity + COMBAT_CONFIG.MOD_BOSS_KILL
        table.insert(modifiers, "падение врага")
    end
    
    -- Соло-выживший
    local survivors = 0
    local lastSurvivor = nil
    for _, p in ipairs(session.participants) do
        if p.deaths == 0 then
            survivors = survivors + 1
            lastSurvivor = p
        end
    end
    
    if survivors == 1 and #session.participants > 1 then
        severity = severity + COMBAT_CONFIG.MOD_SOLO_SURVIVOR
        table.insert(modifiers, "единственный выживший")
    end
    
    -- Итоговый шанс
    local finalChance = math.min(COMBAT_CONFIG.MAX_CHANCE, 
                                  COMBAT_CONFIG.BASE_CHANCE_BOT + severity)
    
    -- Выбираем говорящего (случайный живой бот)
    local speaker = nil
    local aliveBots = {}
    for _, p in ipairs(session.participants) do
        if p.deaths == 0 then table.insert(aliveBots, p) end
    end
    
    if #aliveBots > 0 then
        speaker = aliveBots[math.random(1, #aliveBots)]
    end
    
    if not speaker then
        Log("No survivors to comment on combat")
        combatSessions[guid] = nil
        return
    end
    
    -- Ролл шанса
    local roll = math.random(1, 100)
    Log("Combat ended. Chance: " .. finalChance .. "%, rolled: " .. roll)
    
    if roll > finalChance then
        Log("No post-combat phrase (roll failed)")
        combatSessions[guid] = nil
        return
    end
    
    -- Формируем РП-данные для Python
    local rpData = {
        speaker_guid = speaker.guid,
        speaker_name = speaker.name,
        speaker_class = speaker.class,
        speaker_race = speaker.race,
        duration_desc = DescribeDuration(session.duration),
        duration_sec = session.duration,
        severity = severity,
        modifiers = modifiers,
        casualties = {},
        wounded = {},
        heroes = {},
        boss_name = session.boss_name or nil,
        enemy_count = #session.enemies,
    }
    
    for _, p in ipairs(session.participants) do
        if p.deaths > 0 then
            table.insert(rpData.casualties, p.name)
        else
            local lost = (p.hp_start or 100) - (p.hp_end or 0)
            if lost >= 60 then
                table.insert(rpData.wounded, { name = p.name, state = "на грани смерти" })
            elseif lost >= 30 then
                table.insert(rpData.wounded, { name = p.name, state = "серьёзно ранен" })
            end
            
            -- Герой: выжил при тяжёлых потерях
            if lost >= 50 and (#rpData.casualties > 0 or session.duration > 180) then
                table.insert(rpData.heroes, p.name)
            end
        end
    end
    
    -- Задержка перед фразой
    --local delay = math.random(COMBAT_CONFIG.DELAY_MIN, COMBAT_CONFIG.DELAY_MAX)
    
    -- Сериализуем в JSON
    local jsonData = json.encode(rpData)
    
    -- Записываем в БД через ai_requests
    -- Используем speaker как "игрок" (он говорит), а leader группы как target
    -- FIX v5.1: player_guid = leader (живой игрок получает ответ)
    --            npc_guid = speaker (бот, который говорит фразу)
    -- FIX: убрана искусственная задержка, LLM и так даст 3-4 сек паузы
    local sql = string.format(
        "INSERT INTO ai_requests " ..
        "(player_guid, player_name, npc_guid, npc_entry, npc_name, message, channel_type, target_is_player, created_at) " ..
        "VALUES (%u, '%s', %u, %d, '%s', '%s', '%s', %d, UNIX_TIMESTAMP())",
        session.leader_guid, EscapeSQL(session.leader_name), speaker.guid, 0,
        EscapeSQL(speaker.name), EscapeSQL(jsonData), "POST-COMBAT", 1
    )
    local status, err = pcall(function() CharDBExecute(sql) end)
    if status then
        Log("Post-combat phrase queued for " .. speaker.name .. " in " .. delay .. " sec")
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

Log("Living Azeroth [v5.0] loaded!")
Log("NPC prefix: '" .. AI_WORLD.NPC_PREFIX .. "'")
Log("Usage: /s message — bots respond")
Log("Usage: /s №[NPCName] message — talk to NPC")
Log("Bot-to-bot replies: " .. (AI_WORLD.BOT_REPLY_TO_BOT_CHANCE > 0 and AI_WORLD.BOT_REPLY_TO_BOT_CHANCE .. "% chance, max depth 2" or "DISABLED"))
Log("CombatAnalyst: post-combat phrases enabled")

CreateLuaEvent(GlobalPollLoop, 500, 0)
Log("GlobalPollLoop started (500ms)")