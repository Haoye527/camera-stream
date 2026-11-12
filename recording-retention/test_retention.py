import datetime as dt
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retention import Retention


class RetentionTests(unittest.TestCase):
    def test_rotation_is_global_oldest_first_and_stops_below_85_95(self):
        with tempfile.TemporaryDirectory() as folder:
            app_root = Path(folder).resolve()
            root = app_root / 'recording-volume' / 'easynvr_rec'
            cameras = [{'channel_id': name+'_01', 'device_id': name, 'number': '01'} for name in ('front', 'side')]
            stamp = dt.datetime.now() - dt.timedelta(hours=2)
            def create(camera, start, duration=61000, modified=None):
                path = root / camera / '01' / start.strftime('%Y%m%d') / f'{start:%Y%m%d%H%M%S}-{duration}.mp4'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'0123456789')
                mtime = (modified or start).timestamp()
                os.utime(path, (mtime, mtime))
                return path
            oldest = create('side', stamp)
            newer = create('front', stamp + dt.timedelta(minutes=2))
            partial = create('front', stamp - dt.timedelta(minutes=10), duration=0)
            recently_modified = create('side', stamp - dt.timedelta(minutes=20), modified=dt.datetime.now())
            snapshot = oldest.with_suffix('.jpg')
            snapshot.write_bytes(b'snapshot')
            config = app_root / 'config.toml'
            def configure(fallback):
                config.write_text(f"[RecordInfo]\nPath = '{root.parent}'\nEnableSubdirectory = true\nSubdirectoryName = ''\nDiskUsageRatio = {fallback}\n", encoding='utf-8')
            configure(85.95)
            app = SimpleNamespace(records=root, root=app_root/'preview', cameras=lambda: cameras,
                                  storage_lock=threading.Lock(), storage_cache=['cached'])
            worker = Retention(app, Mock(), config)
            def disk_usage(_):
                used = 8595 if oldest.exists() else 8585
                return shutil._ntuple_diskusage(10000, used, 10000-used)
            with patch('retention.shutil.disk_usage', side_effect=disk_usage) as disk:
                worker.rotate()
                disk.assert_not_called()
                self.assertTrue(oldest.exists())
                configure(95.95)
                worker.rotate()
            self.assertFalse(oldest.exists())
            self.assertTrue(newer.exists())
            self.assertTrue(partial.exists())
            self.assertTrue(recently_modified.exists())
            self.assertTrue(snapshot.exists())
            self.assertEqual(worker.state['removed_this_check'], 1)
            self.assertEqual(worker.state['limit_percent'], 85.95)
            self.assertIsNone(app.storage_cache)


if __name__ == '__main__':
    unittest.main()
