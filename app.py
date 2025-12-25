import os
import json
import hashlib
import hmac
import logging
import time
import urllib.parse
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import redis
from datetime import datetime, timedelta

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

CORS(app, origins=['*'], methods=['GET', 'POST', 'OPTIONS'], allow_headers=['Content-Type', 'X-Init-Data'])

limiter = Limiter(
    app=app,
    key_func=lambda: require_auth() or get_remote_address(),
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.environ.get('REDIS_URL', 'memory://')
)

# Конфигурация DB
def get_db_connection():
    logging.info(f"DATABASE_URL is {'set' if 'DATABASE_URL' in os.environ else 'not set'}")
    try:
        return psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        raise

BOT_TOKEN = os.environ.get('BOT_TOKEN')  # Установить в переменных окружения

def safe_json_loads(data, default):
    try:
        return json.loads(data)
    except Exception as e:
        logging.warning(f"Failed to parse JSON: {e}")
        return default

# Функция для миграции существующих таблиц
def migrate_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Изменить тип id в users на BIGINT
        cursor.execute('ALTER TABLE users ALTER COLUMN id TYPE BIGINT')
        # Изменить FK в maps
        cursor.execute('ALTER TABLE maps ALTER COLUMN creator_id TYPE BIGINT')
        # В raid_sessions
        cursor.execute('ALTER TABLE raid_sessions ALTER COLUMN player_id TYPE BIGINT')
        # В transactions
        cursor.execute('ALTER TABLE transactions ALTER COLUMN user_id TYPE BIGINT')
        # В social
        cursor.execute('ALTER TABLE social ALTER COLUMN user_id TYPE BIGINT')
        cursor.execute('ALTER TABLE social ALTER COLUMN friend_id TYPE BIGINT')
        conn.commit()
        logging.info("Tables migrated successfully")
    except Exception as e:
        logging.warning(f"Migration error (possibly already migrated): {e}")
        conn.rollback()
    finally:
        conn.close()

# Функция для создания таблиц
def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0,
            builder_credits INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_id ON users(id)')

    # Maps
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS maps (
            id SERIAL PRIMARY KEY,
            creator_id BIGINT,
            grid_json TEXT,
            dug_json TEXT DEFAULT '[]',
            difficulty REAL DEFAULT 1.0,
            active BOOLEAN DEFAULT TRUE,
            is_archived BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (creator_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_maps_creator_id ON maps(creator_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_maps_active ON maps(active)')

    # RaidSessions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS raid_sessions (
            id SERIAL PRIMARY KEY,
            player_id BIGINT,
            map_id INTEGER,
            current_stage INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            earnings_buffer REAL DEFAULT 0.0,
            dug_history TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES users(id),
            FOREIGN KEY (map_id) REFERENCES maps(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_raid_sessions_player_id ON raid_sessions(player_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_raid_sessions_status ON raid_sessions(status)')

    # Transactions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount REAL,
            type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)')

    # Social
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS social (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            friend_id BIGINT,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (friend_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_social_user_id ON social(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_social_friend_id ON social(friend_id)')

    conn.commit()
    conn.close()

# Middleware для проверки initData
def validate_init_data(init_data):
    if init_data == 'mock_init_data':
        return 1  # For testing

    logging.info(f"BOT_TOKEN is {'set' if BOT_TOKEN else 'not set'}")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set")

    # Разбор initData с использованием parse_qsl для корректной обработки
    try:
        data = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception as e:
        logging.warning(f"Failed to parse initData: {e}")
        return None

    logging.debug(f"Parsed data keys: {list(data.keys())}")

    if 'auth_date' not in data or time.time() - int(data['auth_date']) >= 86400:
        logging.warning("auth_date missing or expired")
        return None

    if 'hash' not in data:
        logging.warning("No hash in initData")
        return None

    received_hash = data.pop('hash')
    data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    logging.debug(f"Calculated hash: {calculated_hash}")
    logging.debug(f"Received hash: {received_hash}")

    if hmac.compare_digest(received_hash, calculated_hash):
        if 'user' in data:
            try:
                user_data = json.loads(data['user'])
                return user_data.get('id')
            except Exception as e:
                logging.warning(f"Failed to parse user data: {e}")
                return None
        return None
    else:
        logging.warning(f"Hash mismatch: received={received_hash}, calculated={calculated_hash}")
        return None

def require_auth():
    init_data = request.headers.get('X-Init-Data') or request.json.get('initData') if request.is_json else None
    logging.info(f"init_data provided: {bool(init_data)}")
    if not init_data:
        logging.warning("No init_data provided")
        return None
    user_id = validate_init_data(init_data)
    if not user_id:
        logging.warning("Invalid init_data")
        return None
    return user_id

@app.route('/')
def index():
    return render_template('index.html')

# Эндпоинты

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Проверить, есть ли пользователь
        cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()

        if not user:
            # Создать нового пользователя
            cursor.execute('INSERT INTO users (id, username, balance, builder_credits) VALUES (%s, %s, 1.0, 5)', (user_id, f'user_{user_id}'))
            conn.commit()
            user = {'id': user_id, 'username': f'user_{user_id}', 'balance': 1.0, 'builder_credits': 5}

        # Проверить активную сессию
        cursor.execute('SELECT * FROM raid_sessions WHERE player_id = %s AND status = %s', (user_id, 'active'))
        session = cursor.fetchone()

        conn.close()

        response = {
            'user': dict(user),
            'active_session': dict(session) if session else None
        }
        return jsonify(response)
    except Exception as e:
        logging.error(f"Error in login: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/editor/template', methods=['POST'])
def editor_template():
    user_id = require_auth()
    if not user_id:
        # For testing, allow mock
        data = request.json
        if data and data.get('initData') == 'mock_init_data':
            user_id = 1
        else:
            return jsonify({'error': 'Unauthorized'}), 401

    # Генерировать шаблон: 48 клеток, стены рандомно
    import random
    grid = [0] * 48  # 0 - пусто, 1 - стена
    walls = random.sample(range(48), 16)  # 16 стен
    for w in walls:
        grid[w] = 1

    return jsonify({'grid': grid})

@app.route('/api/maps/create', methods=['POST'])
def maps_create():
    user_id = require_auth()
    logging.info(f"user_id: {user_id}")
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    logging.info(f"data: {data}")
    grid = data.get('grid')
    logging.info(f"grid: type={type(grid)}, len={len(grid) if isinstance(grid, list) else 'not list'}")
    if not grid or len(grid) != 48:
        return jsonify({'error': 'Invalid grid'}), 400

    # Валидация
    snakes = sum(1 for x in grid if x == 2)
    holes = sum(1 for x in grid if x == 3)
    chests = sum(1 for x in grid if x == 4)
    walls = sum(1 for x in grid if x == 1)
    logging.info(f"counts: snakes={snakes}, holes={holes}, chests={chests}, walls={walls}")

    if snakes != 4 or holes != 2 or chests != 2:  # Сундук занимает 2 клетки
        return jsonify({'error': 'Invalid placement'}), 400

    # Проверить соседство сундука
    chest_positions = [i for i, x in enumerate(grid) if x == 4]
    logging.info(f"chest_positions: {chest_positions}")
    if len(chest_positions) != 2 or abs(chest_positions[0] - chest_positions[1]) != 1:
        return jsonify({'error': 'Chest must be adjacent'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверить кредиты
    cursor.execute('SELECT builder_credits FROM users WHERE id = %s', (user_id,))
    credits_row = cursor.fetchone()
    if not credits_row or credits_row['builder_credits'] <= 0:
        conn.close()
        return jsonify({'error': 'No credits'}), 400

    # Списать кредит
    cursor.execute('UPDATE users SET builder_credits = builder_credits - 1 WHERE id = %s', (user_id,))

    # Сохранить карту
    cursor.execute('INSERT INTO maps (creator_id, grid_json) VALUES (%s, %s)', (user_id, json.dumps(grid)))

    conn.commit()
    conn.close()

    return jsonify({'success': True})

@app.route('/api/raid/scout', methods=['POST'])
def raid_scout():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()

    # Найти случайную карту
    cursor.execute('SELECT id, creator_id FROM maps WHERE active = TRUE AND creator_id != %s ORDER BY RANDOM() LIMIT 1', (user_id,))
    map_row = cursor.fetchone()
    if not map_row:
        conn.close()
        return jsonify({'error': 'No maps available'}), 404

    map_id = map_row['id']
    # Статистика: просто заглушка, в реальности считать из transactions или добавить поля
    stats = {'deaths': 0, 'wins': 0}
    fee = 0.1  # Пример

    conn.close()

    return jsonify({'map_id': map_id, 'stats': stats, 'fee': fee})

@limiter.limit("1 per minute")
@app.route('/api/raid/start', methods=['POST'])
def raid_start():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    map_id = data.get('map_id')
    if not map_id:
        return jsonify({'error': 'Missing map_id'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверить баланс
    cursor.execute('SELECT balance FROM users WHERE id = %s', (user_id,))
    balance_row = cursor.fetchone()
    balance = balance_row['balance'] if balance_row else 0.0
    fee = 0.1  # Пример
    if balance < fee:
        conn.close()
        return jsonify({'error': 'Insufficient balance'}), 400

    # Проверить существование карты
    cursor.execute('SELECT id FROM maps WHERE id = %s', (map_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Map not found'}), 404

    # Списать fee
    cursor.execute('UPDATE users SET balance = balance - %s WHERE id = %s', (fee, user_id))
    cursor.execute('INSERT INTO transactions (user_id, amount, type) VALUES (%s, %s, %s)', (user_id, -fee, 'raid_entry'))

    # Создать сессию
    expires_at = datetime.now() + timedelta(seconds=120)
    cursor.execute('INSERT INTO raid_sessions (player_id, map_id, expires_at) VALUES (%s, %s, %s) RETURNING id', (user_id, map_id, expires_at))
    session_id = cursor.fetchone()['id']

    # Получить стены из карты
    cursor.execute('SELECT grid_json, dug_json FROM maps WHERE id = %s', (map_id,))
    map_data = cursor.fetchone()
    if not map_data:
        conn.close()
        return jsonify({'error': 'Map not found'}), 404
    grid = json.loads(map_data[0])
    dug = json.loads(map_data[1])
    walls = [i for i, x in enumerate(grid) if x == 1]

    conn.commit()
    conn.close()

    return jsonify({'session_id': session_id, 'walls': walls, 'dug': dug})

@limiter.limit("20 per minute")
@app.route('/api/raid/dig', methods=['POST'])
def raid_dig():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    session_id = data.get('session_id')
    cell_index = data.get('cell_index')
    if session_id is None or cell_index is None or cell_index < 0 or cell_index >= 48:
        return jsonify({'error': 'Missing or invalid data'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Получить сессию
    cursor.execute('SELECT * FROM raid_sessions WHERE id = %s AND player_id = %s', (session_id, user_id))
    session = cursor.fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404

    if datetime.now() > datetime.fromisoformat(session['expires_at']):
        cursor.execute('UPDATE raid_sessions SET status = %s WHERE id = %s', ('timeout', session_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'timeout'})

    map_id = session['map_id']
    dug_history = safe_json_loads(session['dug_history'], [])

    if cell_index in dug_history:
        conn.close()
        return jsonify({'error': 'Cell already dug'}), 400

    # Получить карту
    cursor.execute('SELECT grid_json, dug_json FROM maps WHERE id = %s', (map_id,))
    map_data = cursor.fetchone()
    grid = safe_json_loads(map_data['grid_json'], None)
    if grid is None:
        conn.close()
        return jsonify({'error': 'Map data corrupted'}), 500
    dug = safe_json_loads(map_data['dug_json'], None)
    if dug is None:
        conn.close()
        return jsonify({'error': 'Map data corrupted'}), 500

    cell_type = grid[cell_index]

    if cell_type == 2:  # Змея
        cursor.execute('UPDATE raid_sessions SET status = %s WHERE id = %s', ('dead', session_id))
        dug.append(cell_index)  # Добавить череп
        cursor.execute('UPDATE maps SET dug_json = %s WHERE id = %s', (json.dumps(dug), map_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'dead', 'reward': 0})

    elif cell_type == 3:  # Дыра
        cursor.execute('UPDATE raid_sessions SET status = %s WHERE id = %s', ('hurt', session_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'hurt'})

    else:  # Сундук или пусто
        reward = 0.05 if cell_type == 4 else 0.01  # Пример
        earnings_buffer = session['earnings_buffer'] + reward
        cursor.execute('UPDATE raid_sessions SET earnings_buffer = %s, dug_history = %s WHERE id = %s', (earnings_buffer, json.dumps(dug_history + [cell_index]), session_id))
        dug.append(cell_index)
        cursor.execute('UPDATE maps SET dug_json = %s WHERE id = %s', (json.dumps(dug), map_id))

        # Проверка победы
        safe_cells = sum(1 for x in grid if x in [0, 4])
        opened = len(dug)
        stage_complete = opened >= 12  # Пример

        conn.commit()
        conn.close()
        return jsonify({'status': 'safe', 'reward': reward, 'stage_complete': stage_complete})

@app.route('/api/raid/leave', methods=['POST'])
def raid_leave():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    session_id = data.get('session_id')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT earnings_buffer FROM raid_sessions WHERE id = %s AND player_id = %s', (session_id, user_id))
    session = cursor.fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404

    earnings = session['earnings_buffer']
    cursor.execute('UPDATE users SET balance = balance + %s WHERE id = %s', (earnings, user_id))
    cursor.execute('INSERT INTO transactions (user_id, amount, type) VALUES (%s, %s, %s)', (user_id, earnings, 'raid_win'))
    cursor.execute('UPDATE raid_sessions SET status = %s WHERE id = %s', ('completed', session_id))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'earnings': earnings})

@app.route('/api/my_tombs', methods=['POST'])
def my_tombs():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT id, dug_json, grid_json FROM maps WHERE creator_id = %s', (user_id,))
    tombs = cursor.fetchall()

    result = []
    for tomb in tombs:
        dug = json.loads(tomb['dug_json'])
        grid = json.loads(tomb['grid_json'])
        safe_cells = sum(1 for i, x in enumerate(grid) if x in [0, 4] and i not in dug)
        # Статистика: заглушка
        result.append({
            'id': tomb['id'],
            'deaths': len([x for x in dug if grid[x] == 2]),  # Пример
            'earnings': 0.0,
            'can_claim': safe_cells < 12
        })

    conn.close()
    return jsonify({'tombs': result})

@app.route('/api/my_tombs/claim', methods=['POST'])
def my_tombs_claim():
    user_id = require_auth()
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    map_id = data.get('map_id')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT grid_json, dug_json FROM maps WHERE id = %s AND creator_id = %s', (map_id, user_id))
    map_data = cursor.fetchone()
    if not map_data:
        conn.close()
        return jsonify({'error': 'Map not found'}), 404

    grid = json.loads(map_data['grid_json'])
    dug = json.loads(map_data['dug_json'])
    safe_cells = sum(1 for i, x in enumerate(grid) if x in [0, 4] and i not in dug)
    if safe_cells >= 12:  # Пример цели
        conn.close()
        return jsonify({'error': 'Not ready to claim'}), 400

    # Деактивировать карту
    cursor.execute('UPDATE maps SET active = FALSE WHERE id = %s', (map_id,))

    # Награда: заглушка
    reward = 1.0
    cursor.execute('UPDATE users SET balance = balance + %s WHERE id = %s', (reward, user_id))
    cursor.execute('INSERT INTO transactions (user_id, amount, type) VALUES (%s, %s, %s)', (user_id, reward, 'claim'))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'reward': reward})

try:
    create_tables()
    migrate_tables()
    logging.info("Tables created and migrated successfully")
except Exception as e:
    logging.error(f"Error creating/migrating tables: {e}")

@app.errorhandler(500)
def internal_error(error):
    logging.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(429)
def ratelimit_error(e):
    return jsonify({'error': 'Too many requests', 'retry_after': e.description}), 429

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
