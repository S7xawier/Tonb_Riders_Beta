import requests
import time

BASE_URL = 'http://localhost:5000'

def test_dig_limits():
    print("Testing /api/raid/dig limits (10 per minute)")
    for i in range(12):
        try:
            response = requests.post(f'{BASE_URL}/api/raid/dig', json={'session_id': 1, 'cell_index': 0})
            print(f"Request {i+1}: {response.status_code}")
            if response.status_code == 429:
                print("Rate limit hit!")
                break
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(0.1)  # small delay

def test_start_limits():
    print("Testing /api/raid/start limits (1 per minute)")
    for i in range(3):
        try:
            response = requests.post(f'{BASE_URL}/api/raid/start', json={'map_id': 1})
            print(f"Request {i+1}: {response.status_code}")
            if response.status_code == 429:
                print("Rate limit hit!")
                break
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(0.1)

if __name__ == '__main__':
    test_dig_limits()
    test_start_limits()