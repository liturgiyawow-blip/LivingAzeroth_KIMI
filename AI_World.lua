--[[ Living Azeroth — Чистый и правильный SQL мост между WoW и Python ]]
local AI_WORLD = {
    SEARCH_RADIUS = 30,   -- Радиус поиска NPC (30 метров)
    POLL_INTERVAL = 500,  -- Проверка ответов в базе каждые 500мс
}

local function Log(msg)
    print("[AI World SQL] " .. tostring(msg))
end

-- ============================================
-- ЗАПИСЬ ЗАПРОСА В БАЗУ ДАННЫХ
-- ============================================
local function WriteRequestToDB(player, npc, message, isParty)
    local pName = tostring(player:GetName()):gsub("'", "''")
    local nName = tostring(npc:GetName()):gsub("'", "''")
    local msg   = tostring(message):gsub("'", "''")
    
    local pGuid = player:GetGUIDLow()
    local nGuid = npc:GetGUIDLow()
    local nEntry = npc:GetEntry()
    local partyFlag = isParty and 1 or 0

    local sql = string.format(
        "INSERT INTO ai_requests (player_guid, player_name, npc_guid, npc_entry, npc_name, message, is_party) " ..
        "VALUES (%u, '%s', %u, %d, '%s', '%s', %d)",
        pGuid, pName, nGuid, nEntry, nName, msg, partyFlag
    )
    
    local status, err = pcall(function() CharDBExecute(sql) end)
    if status then
        Log("Запрос записан в БД для: " .. nName)
        return true
    else
        player:SendBroadcastMessage("|cffff0000[AI Ошибка SQL]|r Ошибка: " .. tostring(err))
        Log("КРИТИЧЕСКАЯ ОШИБКА SQL ЗАПИСИ: " .. tostring(err))
        return false
    end
end

-- ============================================
-- ОПРОС ОТВЕТОВ ИЗ БАЗЫ ДАННЫХ
-- ============================================
local function PollResponseFromDB(eventId, delay, repeats, playerGuid, npcGuid)
    local player = GetPlayerByGUID(playerGuid)
    if not player then return end

    local sql = string.format(
        "SELECT id, response_text, emote_id, action_command FROM ai_responses " ..
        "WHERE player_guid = %u AND npc_guid = %u AND fetched = 0 LIMIT 1",
        playerGuid, npcGuid
    )

    local query = CharDBQuery(sql)
    if query then
        local rowId = query:GetUInt32(0)
        local text = query:GetString(1)
        local emoteId = query:GetUInt32(2)
        local actionCmd = query:GetString(3)

        CharDBExecute("UPDATE ai_responses SET fetched = 1 WHERE id = " .. rowId)

        local npc = player:GetMap():GetCreature(npcGuid)
        if npc then
            npc:SendUnitSay(text, 0)
            if emoteId > 0 then 
                npc:PerformEmote(emoteId) 
            end
        else
            player:SendBroadcastMessage("|cff00ff00[AI Ответ]|r " .. text)
        end

        player:RemoveEventById(eventId)
    end
end

-- ============================================
-- ПЕРЕХВАТ СООБЩЕНИЙ ИГРОКА
-- ============================================
local function OnPlayerChat(event, player, msg, msgType, lang)
    if not msg or #msg < 2 or msg:sub(1,1) == "." then return end

    player:SendBroadcastMessage("|cff00ccff[AI Дебаг]|r Чат пойман! Текст: " .. msg)

    -- 1. РАЗГОВОР С ОБЫЧНЫМИ NPC (Белый чат / SAY)
    if msgType == 1 then
        local creatures = player:GetCreaturesInRange(AI_WORLD.SEARCH_RADIUS)
        local targetNpc = nil
        
        for _, c in ipairs(creatures) do
            -- Убираем IsHostileTo. Проверяем только существование, Entry и что оно живое
            if c and c.GetEntry and c:GetEntry() > 0 and c:IsAlive() then
                targetNpc = c
                break
            end
        end

        if targetNpc then
            player:SendBroadcastMessage("|cff00ccff[AI Дебаг]|r Цель рядом: " .. targetNpc:GetName())
            
            if WriteRequestToDB(player, targetNpc, msg, false) then
                player:SendBroadcastMessage("|cff00ff00[AI Дебаг]|r Запрос ушел в MySQL! Ждем Python...")
                player:RegisterEvent(function(id, delay, reps) 
                    PollResponseFromDB(id, delay, reps, player:GetGUIDLow(), targetNpc:GetGUIDLow()) 
                end, AI_WORLD.POLL_INTERVAL, 0)
            end
        else
            player:SendBroadcastMessage("|cffffaa00[AI Дебаг]|r Рядом нет NPC в радиусе " .. AI_WORLD.SEARCH_RADIUS .. " метров.")
        end

    -- 2. ОБЩЕНИЕ С БОТОМ В ГРУППЕ (Чат группы / PARTY)
    elseif msgType == 2 then
        local group = player:GetGroup()
        if not group then return end

        local members = group:GetMembers()
        for _, member in ipairs(members) do
            if member and member:GetGUIDLow() ~= player:GetGUIDLow() then
                local botName = member:GetName()
                if msg:lower():find(botName:lower()) or msg:lower():find("бро") or msg:lower():find("пати") then
                    player:SendBroadcastMessage("|cff00ccff[AI Дебаг]|r Поймали обращение к боту: " .. botName)
                    if WriteRequestToDB(player, member, msg, true) then
                        player:SendBroadcastMessage("|cff00ff00[AI Дебаг]|r Запрос по боту ушел в MySQL!")
                        player:RegisterEvent(function(id, delay, reps) 
                            PollResponseFromDB(id, delay, reps, player:GetGUIDLow(), member:GetGUIDLow()) 
                        end, AI_WORLD.POLL_INTERVAL, 0)
                    end
                    break
                end
            end
        end
    end
end

RegisterPlayerEvent(18, OnPlayerChat)
Log("Модуль Living Azeroth [БЕЗБЕГОВАЯ ВЕРСИЯ] запущен!")