-- ============================================
-- COMBAT TRIGGERS MODULE v5.4
-- Вынесено из AI_World.lua для безопасного редактирования
-- ============================================

if _G.CombatTriggersLoaded then
    print("[CombatTriggers] Already loaded!")
    return
end
_G.CombatTriggersLoaded = true

print("[CombatTriggers] === TRIGGERS MODULE v5.4 LOADING ===")

-- Локальный логгер (на случай, если AI_World ещё не загружен)
local function Log(msg)
    if _G.Log then
        _G.Log(msg)
    else
        print("[CombatTriggers] " .. tostring(msg))
    end
end

-- ═══════════════════════════════════════════════════════════════
-- КОНФИГУРАЦИЯ ШАНСОВ И ПОРОГОВ
-- ═══════════════════════════════════════════════════════════════
_G.COMBAT_CONFIG = {
    BASE_CHANCE_BOT = 30,
    MAX_CHANCE = 100,
    
    THRESHOLD_WOUNDED_HP_LOST = 30,
    THRESHOLD_CRITICAL_HP_LOST = 60,
    THRESHOLD_HERO_HP_LOST = 50,
    THRESHOLD_LONG_FIGHT_SEC = 180,
    THRESHOLD_BOSS_HP = 100000,
    THRESHOLD_BOSS_RANK = 2,
    
    THRESHOLD_HEALER_MANA_LOST = 70,
    
    -- ═══════════════════════════════════════════════════════════
    -- НОВОЕ v5.4: МОДИФИКАТОРЫ ДЛИТЕЛЬНОСТИ БОЯ
    -- ═══════════════════════════════════════════════════════════
    -- Чем короче бой — тем меньше шанс реплики и проще фразы
    -- ═══════════════════════════════════════════════════════════
    DURATION_MODIFIERS = {
        { max_sec = 5,    chance_bonus = -20, min_chance = 10,  category = "instant" },
        { max_sec = 10,   chance_bonus = -5, min_chance = 10, category = "quick" },
        { max_sec = 20,   chance_bonus = 0,   min_chance = 15, category = "short" },
        { max_sec = 60,  chance_bonus = 10,  min_chance = 45, category = "medium" },
        { max_sec = 300,  chance_bonus = 25,  min_chance = 60, category = "long" },
        { max_sec = 9999, chance_bonus = 40,  min_chance = 75, category = "epic" },
    },
}

-- ═══════════════════════════════════════════════════════════════
-- МОДИФИКАТОРЫ ШАНСА
-- Добавляй новые триггеры просто в эту таблицу!
-- ═══════════════════════════════════════════════════════════════
_G.COMBAT_MODIFIERS = {
    { id = "easy_fight",          name = "лёгкий бой",            value = 0,   check = "Check_EasyFight" },
    { id = "wounded",             name = "ранения",               value = 15,  check = "Check_Wounded" },
    { id = "critically_wounded",  name = "тяжёлые ранения",       value = 30,  check = "Check_CriticallyWounded" },
    { id = "death",               name = "потери",                value = 50,  check = "Check_Death" },
    { id = "boss_kill",           name = "падение врага",         value = 20,  check = "Check_BossKill" },
    { id = "long_fight",          name = "долгий бой",            value = 10,  check = "Check_LongFight" },
    { id = "solo_survivor",       name = "единственный выживший", value = 60,  check = "Check_SoloSurvivor" },
    { id = "healer_oom",          name = "хил на пределе",        value = 25,  check = "Check_HealerOOM" },
    { id = "group_health_drop",   name = "группа истекает кровью", value = 20, check = "Check_GroupHealthDrop" },
    { id = "last_stand",          name = "на грани",              value = 35,  check = "Check_LastStand" },
    { id = "pyrrhic_victory",     name = "пиррова победа",        value = 45,  check = "Check_PyrrhicVictory" },
    { id = "iron_bulwark",        name = "стальная стена",        value = 25,  check = "Check_IronBulwark" },
    -- ═══════════════════════════════════════════════════════════
    -- НОВЫЕ ТРИГГЕРЫ v5.4
    -- ═══════════════════════════════════════════════════════════
    { id = "instant_kill",        name = "мгновенное убийство",   value = 0,   check = "Check_InstantKill" },
    { id = "quick_fight",         name = "быстрый бой",           value = 0,   check = "Check_QuickFight" },
    { id = "no_damage",           name = "без единой царапины", value = 5,   check = "Check_NoDamage" },
    { id = "flawless_victory",    name = "безупречная победа",    value = 10,  check = "Check_FlawlessVictory" },
    { id = "overwhelmed",         name = "подавляющее превосходство", value = 0, check = "Check_Overwhelmed" },
    -- ▼▼▼ СЮДА ДОБАВЛЯЙ НОВЫЕ ТРИГГЕРЫ ▼▼▼
}

-- ═══════════════════════════════════════════════════════════════
-- ФУНКЦИИ ПРОВЕРКИ ТРИГГЕРОВ
-- ═══════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════
-- НОВЫЕ ТРИГГЕРЫ v5.4
-- ═══════════════════════════════════════════════════════════

function _G.Check_InstantKill(session, participants)
    -- Бой длился менее 5 секунд И никто не получил урона
    if (session.duration or 0) > 5 then return false end
    
    for _, p in ipairs(participants) do
        local lost = (p.hp_start or 100) - (p.hp_end or 0)
        if lost > 0 then return false end
    end
    
    return true, { duration = session.duration }
end

function _G.Check_QuickFight(session, participants)
    -- Бой 5-15 секунд, без ранений
    local dur = session.duration or 0
    if dur <= 5 or dur > 15 then return false end
    
    for _, p in ipairs(participants) do
        local lost = (p.hp_start or 100) - (p.hp_end or 0)
        if lost >= COMBAT_CONFIG.THRESHOLD_WOUNDED_HP_LOST then return false end
    end
    
    return true, { duration = dur }
end

function _G.Check_NoDamage(session, participants)
    -- Никто не получил урона вообще (для любой длительности)
    for _, p in ipairs(participants) do
        local lost = (p.hp_start or 100) - (p.hp_end or 0)
        if lost > 0 then return false end
    end
    -- Но не мгновенный убийство (уже отдельный триггер)
    if (session.duration or 0) <= 5 then return false end
    return true
end

function _G.Check_FlawlessVictory(session, participants)
    -- Никто не ранен, бой длился > 15 сек (не тривиально)
    if (session.duration or 0) <= 15 then return false end
    
    for _, p in ipairs(participants) do
        local lost = (p.hp_start or 100) - (p.hp_end or 0)
        if lost > 0 then return false end
    end
    
    return true, { duration = session.duration }
end

function _G.Check_Overwhelmed(session, participants)
    -- Группа сильно превосходила врага (все живы, бой быстрый, но не мгновенный)
    local dur = session.duration or 0
    if dur <= 3 or dur > 30 then return false end
    
    for _, p in ipairs(participants) do
        local lost = (p.hp_start or 100) - (p.hp_end or 0)
        if lost > 0 then return false end
    end
    
    -- Должно быть минимум 2 участника
    if #participants < 2 then return false end
    
    return true, { participants = #participants, duration = dur }
end

-- ═══════════════════════════════════════════════════════════
-- СУЩЕСТВУЮЩИЕ ТРИГГЕРЫ (без изменений логики)
-- ═══════════════════════════════════════════════════════════

function _G.Check_IronBulwark(session, participants)
    local tankClasses = { Warrior = true, Paladin = true, Druid = true }
    
    -- Если кто-то умер, стена не выдержала
    for _, p in ipairs(participants) do
        if p.deaths > 0 then return false end
    end
    
    for _, p in ipairs(participants) do
        if tankClasses[p.class] and p.deaths == 0 then
            local lost = (p.hp_start or 100) - (p.hp_end or 0)
            if lost >= 70 then
                return true, { who = p.name, lost = lost }
            end
        end
    end
    return false
end

function _G.Check_PyrrhicVictory(session, participants)
    if not session.boss_killed then return false end
    local total = #participants
    if total == 0 then return false end
    local dead = 0
    local deadNames = {}
    for _, p in ipairs(participants) do
        if p.deaths > 0 then
            dead = dead + 1
            table.insert(deadNames, p.name)
        end
    end
    if dead / total >= 0.5 then
        return true, { count = dead, total = total, names = deadNames }
    end
    return false
end

function _G.Check_LastStand(session, participants)
    if not session.boss_killed then return false end
    for _, p in ipairs(participants) do
        if p.deaths == 0 and (p.hp_end or 100) < 25 then
            return true, { who = p.name, hp_left = p.hp_end }
        end
    end
    return false
end

function _G.Check_GroupHealthDrop(session, participants)
    local totalStart, totalEnd, totalMax = 0, 0, 0
    for _, p in ipairs(participants) do
        totalStart = totalStart + ((p.hp_start or 100) / 100) * (p.max_hp or 1000)
        totalEnd   = totalEnd   + ((p.hp_end   or 0)   / 100) * (p.max_hp or 1000)
        totalMax   = totalMax   + (p.max_hp or 1000)
    end
    if totalMax == 0 then return false end
    
    local dropPct = ((totalStart - totalEnd) / totalMax) * 100
    if dropPct >= 50 then
        return true, { drop = math.floor(dropPct) }
    end
    return false
end

function _G.Check_EasyFight(session, participants)
    for _, p in ipairs(participants) do
        if p.deaths > 0 then return false end
        local lost = (p.hp_start or 100) - (p.hp_end or 0)
        if lost >= COMBAT_CONFIG.THRESHOLD_WOUNDED_HP_LOST then return false end
    end
    return true
end

function _G.Check_Wounded(session, participants)
    for _, p in ipairs(participants) do
        if p.deaths == 0 then
            local lost = (p.hp_start or 100) - (p.hp_end or 0)
            if lost >= COMBAT_CONFIG.THRESHOLD_WOUNDED_HP_LOST and lost < COMBAT_CONFIG.THRESHOLD_CRITICAL_HP_LOST then
                return true, { who = p.name, lost = lost }
            end
        end
    end
    return false
end

function _G.Check_CriticallyWounded(session, participants)
    for _, p in ipairs(participants) do
        if p.deaths == 0 then
            local lost = (p.hp_start or 100) - (p.hp_end or 0)
            if lost >= COMBAT_CONFIG.THRESHOLD_CRITICAL_HP_LOST then
                return true, { who = p.name, lost = lost }
            end
        end
    end
    return false
end

function _G.Check_Death(session, participants)
    local dead = {}
    for _, p in ipairs(participants) do
        if p.deaths > 0 then table.insert(dead, p.name) end
    end
    return #dead > 0, { count = #dead, names = dead }
end

function _G.Check_BossKill(session, participants)
    return session.boss_killed, { name = session.boss_name }
end

function _G.Check_LongFight(session, participants)
    return session.duration > COMBAT_CONFIG.THRESHOLD_LONG_FIGHT_SEC, { duration = session.duration }
end

function _G.Check_SoloSurvivor(session, participants)
    local survivors = 0
    local last = nil
    for _, p in ipairs(participants) do
        if p.deaths == 0 then
            survivors = survivors + 1
            last = p
        end
    end
    return (survivors == 1 and #participants > 1), { who = last and last.name or "unknown" }
end

function _G.Check_HealerOOM(session, participants)
    local healerClasses = { Priest = true, Shaman = true, Paladin = true, Druid = true }
    for _, p in ipairs(participants) do
        if healerClasses[p.class] and p.deaths == 0 then
            local manaLost = (p.mana_start or 100) - (p.mana_end or 0)
            if manaLost >= COMBAT_CONFIG.THRESHOLD_HEALER_MANA_LOST then
                return true, { who = p.name, mana_left = p.mana_end }
            end
        end
    end
    return false
end

-- ═══════════════════════════════════════════════════════════════
-- ДВИЖОК ТРИГГЕРОВ
-- ═══════════════════════════════════════════════════════════════
function _G.EvaluateTriggers(session, participants)
    local severity = 0
    local modifiers = {}
    local triggers = {}
    
    for _, mod in ipairs(COMBAT_MODIFIERS) do
        local checkFunc = _G[mod.check]
        if checkFunc then
            local triggered, details = checkFunc(session, participants)
            if triggered then
                severity = severity + mod.value
                table.insert(modifiers, mod.name)
                triggers[mod.id] = {
                    name = mod.name,
                    value = mod.value,
                    details = details or {},
                }
                Log(string.format("TRIGGER: %s (+%d%%)", mod.name, mod.value))
            end
        else
            Log("WARNING: check function not found: " .. mod.check)
        end
    end
    
    -- ═══════════════════════════════════════════════════════════
    -- НОВОЕ v5.4: ПРИМЕНЕНИЕ МОДИФИКАТОРА ДЛИТЕЛЬНОСТИ
    -- ═══════════════════════════════════════════════════════════
    local duration = session.duration or 0
    local durMod = nil
    for _, mod in ipairs(COMBAT_CONFIG.DURATION_MODIFIERS) do
        if duration <= mod.max_sec then
            durMod = mod
            break
        end
    end
    
    -- Возвращаем также информацию о категории длительности
    -- (AI_World.lua использует её для расчёта finalChance)
    return severity, modifiers, triggers, durMod
end

-- ═══════════════════════════════════════════════════════════════
-- RP-ОПИСАНИЯ
-- ═══════════════════════════════════════════════════════════════
_G.WOUND_DESCRIPTIONS = {
    [0] = "без царапины",
    [1] = "лёгкие царапины",
    [2] = "серьёзные раны",
    [3] = "на грани смерти",
    [4] = "пал в бою",
}

_G.DURATION_DESCRIPTIONS = {
    instant = "мгновенная расправа",
    quick = "краткая схватка",
    short = "краткая схватка",
    medium = "ожесточённый бой",
    long = "долгая, изнурительная резня",
    epic = "битва, о которой будут слагать легенды",
}

function _G.DescribeWoundState(hpStart, hpEnd, deaths)
    if deaths > 0 then return WOUND_DESCRIPTIONS[4] end
    local lost = hpStart - hpEnd
    if lost < 10 then return WOUND_DESCRIPTIONS[0]
    elseif lost < 30 then return WOUND_DESCRIPTIONS[1]
    elseif lost < 60 then return WOUND_DESCRIPTIONS[2]
    else return WOUND_DESCRIPTIONS[3] end
end

function _G.DescribeDuration(seconds)
    if seconds < 5 then return DURATION_DESCRIPTIONS.instant
    elseif seconds < 15 then return DURATION_DESCRIPTIONS.quick
    elseif seconds < 30 then return DURATION_DESCRIPTIONS.short
    elseif seconds < 120 then return DURATION_DESCRIPTIONS.medium
    elseif seconds < 300 then return DURATION_DESCRIPTIONS.long
    else return DURATION_DESCRIPTIONS.epic end
end

Log("CombatTriggers module v5.4 loaded successfully!")