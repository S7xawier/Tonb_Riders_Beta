import unittest
from unittest.mock import patch, MagicMock
import json
import hmac
import hashlib
from app import validate_init_data, app
from flask import request

class TestValidateInitData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot_token = 'test_bot_token'
        cls.patcher = patch('app.BOT_TOKEN', cls.bot_token)
        cls.patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()

    def _generate_hash(self, data_dict):
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(data_dict.items()))
        secret_key = hmac.new('WebAppData'.encode(), self.bot_token.encode(), hashlib.sha256).digest()
        return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    def _create_init_data(self, data_dict, include_hash=True):
        if include_hash:
            hash_value = self._generate_hash(data_dict)
            data_dict = data_dict.copy()
            data_dict['hash'] = hash_value
        return '&'.join(f"{k}={v}" for k, v in data_dict.items())

    def test_valid_init_data(self):
        data = {'user': '{"id":123,"username":"test"}', 'auth_date': '123456'}
        init_data = self._create_init_data(data)
        result = validate_init_data(init_data)
        self.assertEqual(result, 123)

    def test_invalid_hash(self):
        data = {'user': '{"id":123,"username":"test"}', 'auth_date': '123456'}
        init_data = self._create_init_data(data)
        # Replace hash with invalid
        parts = init_data.split('&')
        for i, part in enumerate(parts):
            if part.startswith('hash='):
                parts[i] = 'hash=invalid'
                break
        invalid_init_data = '&'.join(parts)
        result = validate_init_data(invalid_init_data)
        self.assertIsNone(result)

    def test_missing_hash(self):
        data = {'user': '{"id":123,"username":"test"}', 'auth_date': '123456'}
        init_data = self._create_init_data(data, include_hash=False)
        result = validate_init_data(init_data)
        self.assertIsNone(result)

    def test_malformed_user_json(self):
        data = {'user': 'invalid json', 'auth_date': '123456'}
        init_data = self._create_init_data(data)
        result = validate_init_data(init_data)
        self.assertIsNone(result)

    def test_missing_user(self):
        data = {'auth_date': '123456'}
        init_data = self._create_init_data(data)
        result = validate_init_data(init_data)
        self.assertIsNone(result)


class TestRaidDig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.client = cls.app.test_client()
        # Mock database to avoid connection errors during import
        cls.db_patcher = patch('app.get_db_connection')
        cls.mock_get_db = cls.db_patcher.start()
        cls.mock_conn = MagicMock()
        cls.mock_get_db.return_value = cls.mock_conn

    @classmethod
    def tearDownClass(cls):
        cls.db_patcher.stop()

    @patch('app.get_db_connection')
    @patch('app.require_auth')
    def test_normal_dig_safe_cell(self, mock_require_auth, mock_get_db):
        mock_require_auth.return_value = 123
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Mock session
        mock_session = {
            'id': 1,
            'player_id': 123,
            'map_id': 1,
            'status': 'active',
            'earnings_buffer': 0.0,
            'dug_history': '[]',
            'expires_at': '2099-01-01T00:00:00'
        }
        mock_cursor.fetchone.side_effect = [mock_session, {'grid_json': '[0,0,0]', 'dug_json': '[]'}]

        with self.app.test_request_context('/api/raid/dig', method='POST', json={'session_id': 1, 'cell_index': 0}):
            response = self.client.post('/api/raid/dig', json={'session_id': 1, 'cell_index': 0})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['status'], 'safe')

    @patch('app.get_db_connection')
    @patch('app.require_auth')
    def test_corrupted_dug_history(self, mock_require_auth, mock_get_db):
        mock_require_auth.return_value = 123
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Mock session with corrupted dug_history
        mock_session = {
            'id': 1,
            'player_id': 123,
            'map_id': 1,
            'status': 'active',
            'earnings_buffer': 0.0,
            'dug_history': 'invalid json',
            'expires_at': '2099-01-01T00:00:00'
        }
        mock_cursor.fetchone.side_effect = [mock_session, {'grid_json': '[0,0,0]', 'dug_json': '[]'}]

        with self.app.test_request_context('/api/raid/dig', method='POST', json={'session_id': 1, 'cell_index': 0}):
            response = self.client.post('/api/raid/dig', json={'session_id': 1, 'cell_index': 0})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['status'], 'safe')  # Should use [] and proceed

    @patch('app.get_db_connection')
    @patch('app.require_auth')
    def test_corrupted_grid_dug(self, mock_require_auth, mock_get_db):
        mock_require_auth.return_value = 123
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Mock session
        mock_session = {
            'id': 1,
            'player_id': 123,
            'map_id': 1,
            'status': 'active',
            'earnings_buffer': 0.0,
            'dug_history': '[]',
            'expires_at': '2099-01-01T00:00:00'
        }
        # Mock corrupted grid_json
        mock_cursor.fetchone.side_effect = [mock_session, {'grid_json': 'invalid json', 'dug_json': '[]'}]

        with self.app.test_request_context('/api/raid/dig', method='POST', json={'session_id': 1, 'cell_index': 0}):
            response = self.client.post('/api/raid/dig', json={'session_id': 1, 'cell_index': 0})
            self.assertEqual(response.status_code, 500)
            data = response.get_json()
            self.assertIn('error', data)
            self.assertEqual(data['error'], 'Map data corrupted')

    @patch('app.get_db_connection')
    @patch('app.require_auth')
    def test_dig_out_of_bounds(self, mock_require_auth, mock_get_db):
        mock_require_auth.return_value = 123
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Mock session
        mock_session = {
            'id': 1,
            'player_id': 123,
            'map_id': 1,
            'status': 'active',
            'earnings_buffer': 0.0,
            'dug_history': '[]',
            'expires_at': '2099-01-01T00:00:00'
        }
        # Grid with 3 cells, dig cell 5 (out of bounds)
        mock_cursor.fetchone.side_effect = [mock_session, {'grid_json': '[0,0,0]', 'dug_json': '[]'}]

        with self.app.test_request_context('/api/raid/dig', method='POST', json={'session_id': 1, 'cell_index': 5}):
            response = self.client.post('/api/raid/dig', json={'session_id': 1, 'cell_index': 5})
            # Should raise IndexError, but in Flask it will be 500
            self.assertEqual(response.status_code, 500)

    @patch('app.get_db_connection')
    @patch('app.require_auth')
    def test_maps_create_invalid_values(self, mock_require_auth, mock_get_db):
        mock_require_auth.return_value = 123
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Mock credits
        mock_cursor.fetchone.return_value = {'builder_credits': 1}

        # Grid with invalid value 5
        grid = [0] * 45 + [1] * 16 + [2] * 4 + [3] * 2 + [4] * 2 + [5]  # len=48, but 5 invalid

        with self.app.test_request_context('/api/maps/create', method='POST', json={'grid': grid}):
            response = self.client.post('/api/maps/create', json={'grid': grid})
            self.assertEqual(response.status_code, 400)
            data = response.get_json()
            self.assertIn('error', data)

    @patch('app.get_db_connection')
    @patch('app.require_auth')
    def test_raid_start_no_map(self, mock_require_auth, mock_get_db):
        mock_require_auth.return_value = 123
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Mock balance
        mock_cursor.fetchone.side_effect = [{'balance': 1.0}, None]  # No map found

        with self.app.test_request_context('/api/raid/start', method='POST', json={'map_id': 999}):
            response = self.client.post('/api/raid/start', json={'map_id': 999})
            self.assertEqual(response.status_code, 404)
            data = response.get_json()
            self.assertIn('error', data)