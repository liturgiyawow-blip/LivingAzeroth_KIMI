--[[
================================================================================
AI_Tactics.lua v1.0 — Исполнитель тактических планов (Lua-контроллер)
================================================================================

Этот скрипт — "нервная система" ботов. Он работает ВНУТРИ игрового сервера
и имеет доступ к реальному состоянию игры (хп, мана, агро, позиции).

Архитектура:
  Вход:  таблица ai_tactics (Python пишет, Lua читает)
  Логика: проверка условий в реальном времени (каждый тик 200мс)
  Выход: команды ботам через чат (SendChatMessageToPlayer) или прямые методы

ВАЖНО: Этот скрипт должен быть загружен ПОСЛЕ AI_World.lua или отдельно.
================================================================================
]]

if _G.LivingAzerothTacticsLoaded then
    print("[LivingAzerothTactics] Already loaded! Aborting.")
    return
end
_G.LivingAzerothTacticsLoaded = true

print("[LivingAzerothTactics] === TACTICS ENGINE v1.0 LOADING ===")

-- ============================================
-- НАСТРОЙКИ
-- ============================================
local TACTICS = {
    POLL_INTERVAL_MS = 200,         -- Как часто проверять БД (мс)
    EMERGENCY_INTERVAL_MS = 50,     -- Ускоренный режим при emergency
    MAX_STEPS_PER_POLL = 20,        -- Не более N шагов за раз
    DEBUG = true,                   -- Логи в консоль сервера
}

-- ============================================
-- УТИЛИТЫ
-- ============================================
local function Log(msg)
    print("[LivingAzerothTactics] " .. tostring(msg))
end

local function DebugToPlayer(player, msg)
    if TACTICS.DEBUG and player then
        player:SendBroadcastMessage("|cff00ff00[TACTICS]|r " .. tostring(msg))
    end
end

local function EscapeSQL(str)
    if not str then return "" end
    return tostring(str):gsub("\0", ""):gsub("'", "''"):gsub("\\", "\\\\")
end

-- ============================================
-- ПОИСК ИГРОКА ПО GUID
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
-- ПОИСК БОТА ПО GUID
-- ============================================
local function FindBotByGUIDLow(player, guidLow)
    if not player then return nil end
    local group = player:GetGroup()
    if not group then return nil end
    
    local members = group:GetMembers()
    if not members then return nil end
    
    for i = 1, #members do
        local member = members[i]
        if member then
            local ok, low = pcall(function() return member:GetGUIDLow() end)
            if ok and low == guidLow then return member end
        end
    end
    return nil
end

-- ============================================
-- ПОЛУЧЕНИЕ РОЛИ БОТА (как в AI_World.lua)
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
-- ПРОВЕРКА УСЛОВИЙ (САМОЕ ВАЖНОЕ!)
-- ============================================

--[[
Проверить условие шага в реальном времени.

Это "сердце" системы — только Lua внутри игрового цикла может
видеть реальное хп, ману, агро и т.д.

condition_json приходит из БД в формате:
  {"type": "health_below", "target": "leader", "value": 50, "unit": "percent"}

Возвращает: true (условие выполнено, можно действовать) или false
]]
local function CheckCondition(conditionJson, player, bot)
    if not conditionJson or conditionJson == "" or conditionJson == "null" then
        return true  -- Нет условия = выполнять сразу
    end

    -- Парсим JSON (простой парсинг без внешних библиотек)
    local condition = {}
    -- Извлекаем поля из JSON-строки вручную (безопасно для Lua)
    local type_match = conditionJson:match('"type"%s*:%s*"([^"]+)"')
    local target_match = conditionJson:match('"target"%s*:%s*"([^"]+)"')
    local value_match = conditionJson:match('"value"%s*:%s*(%d+)')
    
    if not type_match then
        Log("WARNING: Cannot parse condition: " .. tostring(conditionJson))
        return true  -- Если не парсится — выполняем (безопасный fallback)
    end

    local condType = type_match
    local target = target_match or "leader"
    local value = tonumber(value_match) or 0

    -- Определяем целевого юнита для проверки
    local targetUnit = nil
    if target == "leader" then
        targetUnit = player
    elseif target == "self" then
        targetUnit = bot
    elseif target == "tank" then
        -- Найти танка в группе
        local group = player:GetGroup()
        if group then
            local members = group:GetMembers()
            for i = 1, #members do
                local m = members[i]
                if m and GetBotRole(m) == "tank" then
                    targetUnit = m
                    break
                end
            end
        end
    else
        targetUnit = player  -- По умолчанию — лидер
    end

    if not targetUnit then
        Log("WARNING: No target unit for condition check")
        return false
    end

    -- ═══════════════════════════════════════════════════════════
    -- ПРОВЕРКА ТИПОВ УСЛОВИЙ
    -- ═══════════════════════════════════════════════════════════

    if condType == "health_below" then
        local ok, hpPct = pcall(function() return targetUnit:GetHealthPct() end)
        if ok and hpPct then
            local result = (hpPct < value)
            if TACTICS.DEBUG then
                Log(string.format("Condition health_below: %s HP=%.1f%% (need < %d%%) → %s",
                    target, hpPct, value, tostring(result)))
            end
            return result
        end

    elseif condType == "health_above" then
        local ok, hpPct = pcall(function() return targetUnit:GetHealthPct() end)
        if ok and hpPct then
            local result = (hpPct > value)
            return result
        end

    elseif condType == "mana_below" then
        local ok, manaPct = pcall(function() 
            local powerType = targetUnit:GetPowerType()
            local power = targetUnit:GetPower(powerType)
            local maxPower = targetUnit:GetMaxPower(powerType)
            if maxPower > 0 then return (power / maxPower) * 100 end
            return 100
        end)
        if ok and manaPct then
            local result = (manaPct < value)
            return result
        end

    elseif condType == "threat_above" then
        -- Проверка агро (упрощённая — по наличию в threat list цели)
        local ok, victim = pcall(function() return targetUnit:GetVictim() end)
        if ok and victim then
            -- Если цель бьёт не танка — агро сорвано
            local tank = nil
            local group = player:GetGroup()
            if group then
                local members = group:GetMembers()
                for i = 1, #members do
                    local m = members[i]
                    if m and GetBotRole(m) == "tank" then
                        tank = m
                        break
                    end
                end
            end
            if tank then
                local ok2, tankVictim = pcall(function() return tank:GetVictim() end)
                if ok2 and victim ~= tankVictim then
                    return true  -- Агро сорвано!
                end
            end
        end
        return false

    elseif condType == "enemy_count_above" then
        -- Подсчёт врагов в радиусе 30 ярдов от игрока
        local ok, unfriendly = pcall(function() 
            return player:GetUnfriendlyUnitsInRange(30) 
        end)
        if ok and unfriendly then
            local count = #unfriendly
            return (count > value)
        end
        return false

    elseif condType == "phase_time_elapsed" then
        -- Проверяется внешне (Python ставит метку времени)
        -- Здесь всегда true, т.к. шаг уже в БД = время прошло
        return true

    else
        Log("WARNING: Unknown condition type: " .. condType)
        return true  -- Неизвестное условие = выполняем (безопасно)
    end

    return false
end

-- ============================================
-- ВЫПОЛНЕНИЕ КОМАНДЫ БОТУ
-- ============================================

--[[
Отправить команду боту через чат.

Playerbots понимает текстовые команды в PARTY и WHISPER каналах.
Мы используем WHISPER для точечного управления конкретным ботом.
]]
local function ExecuteBotCommand(player, bot, command, target, targetRti, strategyCmd)
    -- player = лидер группы (отправляет команду), bot = целевой бот
    if not player or not bot or not command then return false end

    local botName = bot:GetName()
    local finalCmd = command

    -- Если есть стратегия — отправляем её (переключает AI бота)
    if strategyCmd and strategyCmd ~= "" and strategyCmd ~= "null" then
        finalCmd = strategyCmd
        Log(string.format("→ %s STRATEGY: %s", botName, finalCmd))
    else
        -- Преобразуем action в команду playerbots
        local actionMap = {
            ["attack"] = "attack",
            ["heal"] = "heal",
            ["stay"] = "stay",
            ["follow"] = "follow",
            ["flee"] = "flee",
            ["pull"] = "pull my target",
            ["wait"] = "stay",
        }

        local mappedCmd = actionMap[command]
        if mappedCmd then
            finalCmd = mappedCmd
        end

        -- Если указана RTI-метка — модифицируем команду
        if targetRti and targetRti ~= "" and targetRti ~= "null" then
            if command == "attack" then
                finalCmd = "attack rti target"
            end
        end

        Log(string.format("→ %s COMMAND: %s (action=%s, target=%s)",
            botName, finalCmd, command, tostring(target)))
    end

    -- ═══════════════════════════════════════════════════════════
    -- FIX: Playerbots перехватывает команды ТОЛЬКО из PARTY чата.
    -- Отправляем команду от лица лидера в PARTY канал (msgType=2).
    -- 
    -- Для точечного управления конкретным ботом добавляем имя бота
    -- в начало команды, если playerbots поддерживает именные команды.
    -- Если нет — команда уйдёт всем ботам (это нормально для @all).
    -- ═══════════════════════════════════════════════════════════
    
    local ok = false
    local partyCmd = finalCmd

    -- Пробуем с именем бота для точечного управления
    -- Формат: "Alfonso attack" — только Alfonso выполнит
    local namedCmd = botName .. " " .. finalCmd

    -- Способ 1: Команда с именем бота (точечное управление)
    ok = pcall(function()
        player:Say(namedCmd, 2)  -- CHAT_PARTY = 2
    end)

    if ok then
        Log("Sent NAMED command to party: '" .. namedCmd .. "'")
        DebugToPlayer(player, "|cff00ff00[TACTICS]|r " .. botName .. " → " .. finalCmd)
        return true
    end

    -- Способ 2: Просто команда без имени (все боты выполнят)
    ok = pcall(function()
        player:Say(partyCmd, 2)  -- CHAT_PARTY = 2
    end)

    if ok then
        Log("Sent GROUP command to party: '" .. partyCmd .. "'")
        DebugToPlayer(player, "|cff00ff00[TACTICS]|r GROUP → " .. partyCmd)
        return true
    end

    Log("WARNING: Failed to send command to party")
    return false
end
-- ============================================
-- ПОМЕТКА ШАГА ВЫПОЛНЕННЫМ
-- ============================================
local function MarkStepCompleted(stepId, resultText)
    if not stepId then return end

    local sql = string.format(
        "UPDATE ai_tactics SET executed = 2, completed_at = UNIX_TIMESTAMP(), result_text = '%s' WHERE id = %d",
        EscapeSQL(resultText or "completed_by_lua"), stepId
    )

    local status, err = pcall(function() CharDBExecute(sql) end)
    if not status then
        Log("ERROR marking step completed: " .. tostring(err))
    else
        Log("Step " .. stepId .. " marked completed")
    end
end

-- ============================================
-- ПОМЕТКА ШАГА ОТМЕНЁННЫМ (при ошибке)
-- ============================================
local function MarkStepFailed(stepId, errorText)
    if not stepId then return end

    local sql = string.format(
        "UPDATE ai_tactics SET executed = 4, completed_at = UNIX_TIMESTAMP(), result_text = '%s' WHERE id = %d",
        EscapeSQL(errorText or "failed"), stepId
    )

    pcall(function() CharDBExecute(sql) end)
end

-- ============================================
-- ЧТЕНИЕ И ОБРАБОТКА ШАГОВ ИЗ БД
-- ============================================

--[[
Главная функция: читает ai_tactics и исполняет шаги.

Вызывается каждые 200мс через CreateLuaEvent.
]]
local function ProcessTacticSteps()
    -- SQL: выбираем шаги в обработке (executed=1) или без условий (executed=0)
    -- Priority: emergency первыми
    local sql = [[
        SELECT 
            id, plan_id, player_guid, player_name,
            bot_guid, bot_name, bot_role,
            phase_id, phase_name, step_id,
            action, target, target_rti, strategy_cmd,
            condition_json, priority, timeout_sec
        FROM ai_tactics
        WHERE executed IN (0, 1)
          AND (created_at + timeout_sec) > UNIX_TIMESTAMP()
        ORDER BY 
            FIELD(priority, 'emergency', 'manual', 'normal'),
            created_at ASC
        LIMIT ]] .. TACTICS.MAX_STEPS_PER_POLL

    local query = CharDBQuery(sql)
    if not query then return end

    local processedCount = 0

    while query:NextRow() do
        local stepId = query:GetUInt32(0)
        local planId = query:GetString(1)
        local playerGuid = query:GetUInt32(2)
        local playerName = query:GetString(3)
        local botGuid = query:GetUInt32(4)
        local botName = query:GetString(5)
        local botRole = query:GetString(6)
        local phaseId = query:GetUInt32(7)
        local phaseName = query:GetString(8)
        local stepIdStr = query:GetString(9)
        local action = query:GetString(10)
        local target = query:GetString(11)
        local targetRti = query:GetString(12)
        local strategyCmd = query:GetString(13)
        local conditionJson = query:GetString(14)
        local priority = query:GetString(15)
        local timeoutSec = query:GetUInt32(16)

        -- Найти игрока-лидера и бота
        local player = FindPlayerByGUIDLow(playerGuid)
        if not player then
            Log("Player " .. playerName .. " offline, skipping step " .. stepId)
            MarkStepFailed(stepId, "player_offline")
            goto continue
        end

        local bot = FindBotByGUIDLow(player, botGuid)
        if not bot then
            Log("Bot " .. botName .. " not found, skipping step " .. stepId)
            MarkStepFailed(stepId, "bot_not_found")
            goto continue
        end

        -- Проверить, жив ли бот
        local okAlive, isAlive = pcall(function() return bot:IsAlive() end)
        if not okAlive or not isAlive then
            Log("Bot " .. botName .. " is dead, skipping")
            MarkStepFailed(stepId, "bot_dead")
            goto continue
        end

        -- ═══════════════════════════════════════════════════════════
        -- ПРОВЕРКА УСЛОВИЯ (реальное время!)
        -- ═══════════════════════════════════════════════════════════
        local conditionMet = CheckCondition(conditionJson, player, bot)

        if not conditionMet then
            -- Условие не выполнено — оставляем шаг в БД для следующего тика
            if TACTICS.DEBUG then
                Log(string.format("Step %d waiting for condition (%s)",
                    stepId, conditionJson or "none"))
            end
            goto continue
        end

        -- ═══════════════════════════════════════════════════════════
        -- ВЫПОЛНЕНИЕ КОМАНДЫ 
        -- ═══════════════════════════════════════════════════════════
        Log(string.format("EXECUTING step %d [%s]: %s → %s (phase=%s, priority=%s)",
            stepId, stepIdStr, action, botName, phaseName, priority))

        local success = ExecuteBotCommand(player, bot, action, target, targetRti, strategyCmd)
        if success then
            MarkStepCompleted(stepId, "executed_by_lua")
            processedCount = processedCount + 1

            -- Для игрока — эффектный вывод
            if priority == "emergency" then
                DebugToPlayer(player, "|cffff0000[EMERGENCY]|r " .. botName .. " выполняет: " .. action)
            else
                DebugToPlayer(player, "|cff00ccff[TACTICS]|r " .. botName .. " → " .. action)
            end
        else
            MarkStepFailed(stepId, "execution_failed")
        end

        ::continue::
    end

    if processedCount > 0 and TACTICS.DEBUG then
        Log("Processed " .. processedCount .. " tactic steps this tick")
    end
end

-- ============================================
-- ОЧИСТКА СТАРЫХ ЗАПИСЕЙ
-- ============================================
local function CleanupOldTactics()
    local sql = [[
        DELETE FROM ai_tactics 
        WHERE executed IN (2, 3, 4) 
        AND completed_at < UNIX_TIMESTAMP() - 3600
        LIMIT 1000
    ]]
    pcall(function() CharDBExecute(sql) end)
end

-- ============================================
-- ГЛАВНЫЙ ЦИКЛ
-- ============================================
local pollCounter = 0

local function TacticsPollLoop()
    pollCounter = pollCounter + 1

    -- Каждые 100 тиков (20 сек) — очистка старых записей
    if pollCounter % 100 == 0 then
        CleanupOldTactics()
    end

    -- Основная обработка
    local ok, err = pcall(ProcessTacticSteps)
    if not ok then
        Log("ERROR in ProcessTacticSteps: " .. tostring(err))
    end
end

-- ============================================
-- РЕГИСТРАЦИЯ И ЗАПУСК
-- ============================================

-- Запускаем polling каждые 200мс (5 раз в секунду)
CreateLuaEvent(TacticsPollLoop, TACTICS.POLL_INTERVAL_MS, 0)

Log("Tactics engine started!")
Log("Polling interval: " .. TACTICS.POLL_INTERVAL_MS .. "ms")
Log("Max steps per poll: " .. TACTICS.MAX_STEPS_PER_POLL)
Log("Waiting for plans from Python...")