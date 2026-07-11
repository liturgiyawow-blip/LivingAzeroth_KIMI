-- ============================================================
-- LivingAzeroth NPC-ONLY Database Schema v1.1
-- Вариант А: Живые NPC
-- Entry 1423: Stormwind City Guard (Goldshire)
-- ============================================================
-- Выполнять в базе acore_characters
-- ============================================================

-- ai_requests — входящие сообщения от игроков (Lua → Python)
CREATE TABLE IF NOT EXISTS ai_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_guid INT UNSIGNED NOT NULL,
    player_name VARCHAR(32) NOT NULL,
    npc_guid INT UNSIGNED NOT NULL,
    npc_entry INT UNSIGNED DEFAULT 0,
    npc_name VARCHAR(64) NOT NULL,
    message TEXT NOT NULL,
    channel_type VARCHAR(32) DEFAULT 'SAY',
    target_is_player TINYINT(1) DEFAULT 0,
    processed TINYINT(1) DEFAULT 0,
    created_at INT UNSIGNED DEFAULT 0
);

-- ai_responses — исходящие ответы (Python → Lua)
CREATE TABLE IF NOT EXISTS ai_responses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_guid INT UNSIGNED NOT NULL,
    npc_guid INT UNSIGNED NOT NULL,
    npc_entry INT UNSIGNED DEFAULT 0,
    response_text VARCHAR(255) NOT NULL,
    emote_id INT UNSIGNED DEFAULT 0,
    action_command VARCHAR(64) DEFAULT NULL,
    mood_change VARCHAR(8) DEFAULT '0',
    fetched TINYINT(1) DEFAULT 0,
    delivered_at INT UNSIGNED DEFAULT 0,
    created_at INT UNSIGNED DEFAULT 0
);

-- ============================================================
-- npc_memory — долгосрочная память NPC
-- ============================================================
CREATE TABLE IF NOT EXISTS npc_memory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    npc_guid INT UNSIGNED NOT NULL,
    npc_entry INT UNSIGNED NOT NULL,
    player_guid INT UNSIGNED NOT NULL,
    player_name VARCHAR(32) NOT NULL,
    memory_type VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    player_message VARCHAR(255),
    npc_response VARCHAR(255),
    mood_after VARCHAR(32),
    reputation_after INT DEFAULT 0,
    location VARCHAR(128),
    created_at INT UNSIGNED DEFAULT 0,
    INDEX idx_npc_player (npc_guid, player_guid),
    INDEX idx_player (player_guid),
    INDEX idx_created (created_at)
);

-- ============================================================
-- npc_reputation — репутация игроков у NPC
-- ============================================================
CREATE TABLE IF NOT EXISTS npc_reputation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    npc_guid INT UNSIGNED NOT NULL,
    npc_entry INT UNSIGNED NOT NULL,
    player_guid INT UNSIGNED NOT NULL,
    player_name VARCHAR(32) NOT NULL,
    reputation INT DEFAULT 0,
    reputation_rank VARCHAR(32) DEFAULT 'neutral',
    total_dialogues INT DEFAULT 0,
    quests_given INT DEFAULT 0,
    quests_completed INT DEFAULT 0,
    last_interaction_at INT UNSIGNED DEFAULT 0,
    UNIQUE KEY uk_npc_player (npc_guid, player_guid),
    INDEX idx_player_rep (player_guid, reputation)
);

-- ============================================================
-- npc_quests — определения квестов
-- ============================================================
CREATE TABLE IF NOT EXISTS npc_quests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    quest_id VARCHAR(64) NOT NULL,
    quest_name VARCHAR(128) NOT NULL,
    quest_description TEXT,
    giver_npc_entry INT UNSIGNED NOT NULL,
    giver_npc_name VARCHAR(64),
    required_item_entry INT UNSIGNED DEFAULT 0,
    required_item_count INT DEFAULT 0,
    required_npc_kills INT DEFAULT 0,
    required_npc_entry INT UNSIGNED DEFAULT 0,
    reward_gold INT DEFAULT 0,
    reward_item_entry INT UNSIGNED DEFAULT 0,
    reward_item_count INT DEFAULT 0,
    reward_reputation INT DEFAULT 0,
    is_repeatable TINYINT(1) DEFAULT 0,
    min_level INT DEFAULT 1,
    max_level INT DEFAULT 80,
    UNIQUE KEY uk_quest_id (quest_id)
);

-- ============================================================
-- player_quest_progress — прогресс квестов игроков
-- ============================================================
CREATE TABLE IF NOT EXISTS player_quest_progress (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_guid INT UNSIGNED NOT NULL,
    player_name VARCHAR(32) NOT NULL,
    quest_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) DEFAULT 'active',
    item_count INT DEFAULT 0,
    npc_kills INT DEFAULT 0,
    given_by_npc_guid INT UNSIGNED,
    given_at INT UNSIGNED DEFAULT 0,
    completed_at INT UNSIGNED DEFAULT 0,
    UNIQUE KEY uk_player_quest (player_guid, quest_id),
    INDEX idx_player (player_guid),
    INDEX idx_quest (quest_id)
);

-- ============================================================
-- НАЧАЛЬНЫЕ ДАННЫЕ
-- ============================================================

-- Квест "Волки Голдшира" — выдаётся стражником entry 1423
INSERT IGNORE INTO npc_quests (
    quest_id, quest_name, quest_description,
    giver_npc_entry, giver_npc_name,
    required_item_entry, required_item_count,
    reward_gold, reward_reputation
) VALUES (
    'wolves_goldshire',
    'Волки у Голдшира',
    'В Элвиннском лесу участились нападения волков. Принеси мне 3 волчьи шкуры как доказательство. Награда: 5 серебра и моя благодарность.',
    1423,
    'Stormwind City Guard',
    2672,
    3,
    500,
    10
);
