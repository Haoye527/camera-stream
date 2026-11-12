"""Space-based rotation of completed EasyNVR recordings, oldest first."""
import datetime as dt
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import threading
import time
import tomllib

from camera_registry import channel_lock, ordinary


LIMIT_BASIS_POINTS = 8595
NATIVE_FALLBACK = 95.95
SEGMENT_NAME = re.compile(r'(\d{14})-([1-9]\d*)\.mp4')
MINIMUM_AGE = dt.timedelta(minutes=2)


def above_limit(usage):
    return usage.used * 10000 >= usage.total * LIMIT_BASIS_POINTS


def directories(path):
    try:
        if ordinary(path) and path.is_dir():
            yield from path.iterdir()
    except FileNotFoundError:
        return


def signature(path):
    if not ordinary(path):
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        return None
    return (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)


def segments(root, cameras, now):
    candidates = []
    cutoff = now - MINIMUM_AGE
    for camera in cameras:
        device = root / camera['device_id']
        if device.parent != root or not ordinary(device):
            continue
        channel = device / camera['number']
        if channel.parent != device:
            continue
        for day in directories(channel):
            if not re.fullmatch(r'\d{8}', day.name):
                continue
            for path in directories(day):
                match = SEGMENT_NAME.fullmatch(path.name)
                if not match or match[1][:8] != day.name:
                    continue
                try:
                    start = dt.datetime.strptime(match[1], '%Y%m%d%H%M%S')
                    end = start + dt.timedelta(milliseconds=int(match[2]))
                    if end > cutoff:
                        continue
                    current = signature(path)
                    if current and dt.datetime.fromtimestamp(current[1] / 1e9) < cutoff:
                        candidates.append((start, camera['channel_id'], path, current))
                except (FileNotFoundError, OverflowError, ValueError):
                    continue
    return sorted(candidates)


class Retention:
    def __init__(self, app, log, config_path=None):
        self.app = app
        self.root = app.records
        self.config = config_path or app.source.with_name('config.toml')
        self.log = log
        self.state = {'status': 'starting', 'limit_percent': LIMIT_BASIS_POINTS / 100}

    def configured(self):
        with self.config.open('rb') as source:
            record = tomllib.load(source)['RecordInfo']
        if not record['EnableSubdirectory']:
            return False
        subdirectory = record['SubdirectoryName'] or 'easynvr_rec'
        configured_root = Path(record['Path']) / subdirectory
        return (record['DiskUsageRatio'] == NATIVE_FALLBACK
                and configured_root == self.root
                and ordinary(configured_root) and configured_root.resolve() == self.root)

    def rotate(self):
        if not self.configured():
            self.state = {**self.state, 'status': 'waiting_for_native_config'}
            return
        usage = shutil.disk_usage(self.root)
        removed = 0
        released = 0
        if above_limit(usage):
            for _, channel, file, original in segments(self.root, self.app.cameras(), dt.datetime.now()):
                try:
                    with channel_lock(channel, self.app.root.parent / 'channel-locks', timeout=.1):
                        usage = shutil.disk_usage(self.root)
                        if not above_limit(usage) or not self.configured():
                            break
                        if file.resolve() != file or not file.is_relative_to(self.root) or signature(file) != original:
                            continue
                        file.unlink()
                        removed += 1
                        released += original[0]
                        self.log.info('overwritten %s (%s bytes)', file.relative_to(self.root), original[0])
                except OSError:
                    continue
            if removed:
                with self.app.storage_lock:
                    self.app.storage_cache = None
        usage = shutil.disk_usage(self.root)
        self.state = {'status': 'above_limit' if above_limit(usage) else 'ready',
                      'limit_percent': LIMIT_BASIS_POINTS / 100,
                      'used_percent': round(100 * usage.used / usage.total, 2),
                      'removed_this_check': removed, 'released_this_check': released,
                      'updated_at': dt.datetime.now().isoformat(timespec='seconds')}

    def run(self):
        while True:
            try:
                self.rotate()
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                self.state = {**self.state, 'status': 'error', 'error': type(error).__name__}
                self.log.warning('rotation check failed: %s', type(error).__name__)
            time.sleep(5)


def start(app):
    log = logging.getLogger('recording-retention')
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(Path(__file__).with_name('rotation.log'), maxBytes=1024*1024,
                                  backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)
    worker = Retention(app, log)
    threading.Thread(target=worker.run, daemon=True, name='recording-retention').start()
    return worker
