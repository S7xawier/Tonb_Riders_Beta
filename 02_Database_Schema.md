02. Схема Базы Данных (PostgreSQL/SQLite)

Все таблицы должны иметь индексы для ускорения поиска.

1. Users (Пользователи)

Основная таблица.

id: BigInt (Telegram ID), Primary Key.

username: String.

balance: Decimal(10, 2) — основной баланс TON.

builder_credits: Integer — попытки для создания карт.

created_at: Timestamp.

2. Maps (Гробницы)

Карты, созданные игроками.

id: Serial, PK.

creator_id: FK на Users.

grid_json: Text/JSONB — полная структура карты (массив из 48 элементов), где хранятся типы клеток (0=стена, 1=пусто, 2=змея, 3=скарабей, 4=сундук). Это секретная информация, никогда не отдается клиенту полностью.

dug_json: Text/JSONB — массив индексов открытых клеток.

difficulty: Float (множитель сложности).

active: Boolean (активна ли карта).

is_archived: Boolean (удалена ли из поиска).

3. RaidSessions (Активные рейды)

Чтобы хранить состояние, когда игрок внутри чужой гробницы.

id: Serial, PK.

player_id: FK на Users.

map_id: FK на Maps.

current_stage: Int (1, 2 или 3).

status: Enum ('active', 'completed', 'dead', 'escaped').

earnings_buffer: Decimal — сколько денег накоплено в этом рейде (но еще не выплачено на баланс).

dug_history: JSONB — какие клетки открыл именно этот игрок в этой сессии.

created_at: Timestamp.

expires_at: Timestamp (для тайм-аута).

4. Transactions (Аудит)

Любое изменение баланса пишется сюда.

id: Serial.

user_id: FK.

amount: Decimal (может быть отрицательным).

type: String ('raid_entry', 'raid_win', 'create_map_fee', 'deposit_return').

created_at: Timestamp.

5. Social (Друзья/Реквесты)

user_id: FK.

friend_id: FK.

status: Enum ('pending', 'accepted').