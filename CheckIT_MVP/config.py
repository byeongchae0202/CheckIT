from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent
ITEMS_FILE = ROOT / 'data' / 'items.json'
MODEL_FILE = ROOT / 'models' / 'yolo11n.pt'
BAUDRATE = 115200
ACK_TIMEOUT = 4.0
PROFILE_DB = Path(os.environ.get('CHECKIT_PROFILE_DB', str(ROOT / 'data' / 'profiles.sqlite3')))


def get_weather_key():
    try:
        from local_settings import KMA_SERVICE_KEY
    except ImportError:
        KMA_SERVICE_KEY = ''
    value = os.environ.get('KMA_SERVICE_KEY', '').strip() or KMA_SERVICE_KEY.strip()
    return '' if value.startswith('여기에_') or value == 'PASTE_KMA_DECODING_KEY_HERE' else value
