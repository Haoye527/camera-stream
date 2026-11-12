import contextlib,datetime as dt,json,pathlib,re,sqlite3,tempfile,unittest
from unittest.mock import Mock,patch
import service

class AdaptiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.settings=json.loads((service.ROOT/'settings.json').read_text())
    def test_merge_clips_and_combines_ranges(self):
        self.assertEqual(service.merge([(-3,2),(1,4),(8,15)],10),[[0.,4],[8,10]])
    def test_status_recovers_from_a_windows_sharing_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            status=root/'status.json'
            status.write_text('{"running":false}')
            state={'running':True}
            sharing_error=PermissionError('status file temporarily locked')
            sharing_error.winerror=5
            replace=pathlib.Path.replace
            with patch.object(service,'ROOT',root), patch.object(pathlib.Path,'replace',autospec=True) as rename, patch.object(service.time,'sleep'):
                rename.side_effect=sharing_error
                with patch('sys.stderr') as log:
                    service.publish_status(state)
                self.assertIn('Unable to publish status',str(log.write.call_args_list))
                self.assertFalse(json.loads(status.read_text())['running'])
                def unlock_after_first_attempt(source,destination):
                    if rename.call_count==1:raise sharing_error
                    return replace(source,destination)
                rename.reset_mock()
                rename.side_effect=unlock_after_first_attempt
                service.publish_status(state)
                self.assertEqual(rename.call_count,2)
                self.assertTrue(json.loads(status.read_text())['running'])
    def test_events_are_scoped_and_preserve_pre_post_across_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            p=pathlib.Path(folder)/'events.db'
            with contextlib.closing(sqlite3.connect(p)) as db:
                db.execute('create table events(channel_id,start_time,duration)')
                db.executemany('insert into events values(?,?,?)',[('front_01','2026-11-06 13:00:28+08:00',2000),('other_01','2026-11-06 13:00:10+08:00',1000)]);db.commit()
            windows=service.event_windows('front_01',dt.datetime(2026,11,6,13),30,self.settings,p)
            self.assertEqual(windows,[[25.,40]])
    def test_filters_keep_event_intervals_and_segment_edges(self):
        expression,windows=service.select_filter([(12,18)],30,self.settings)
        self.assertEqual(windows,[[0.,3],[12,18],[27,30]])
        self.assertIn('0.500000',expression)
    def test_scan_continues_past_removed_and_locked_recordings(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder).resolve()
            removed=root/'20261107160000-61000.mp4'
            locked=root/'20261107160101-61000.mp4'
            available=root/'20261107160202-61000.mp4'
            locked.write_bytes(b'locked recording')
            available.write_bytes(b'completed recording')
            base=Mock()
            base.glob.side_effect=lambda pattern:iter([removed,locked,available])
            lstat=pathlib.Path.lstat
            def deny_locked_file(path):
                if path==locked:raise PermissionError('recording temporarily unavailable')
                return lstat(path)
            with contextlib.closing(sqlite3.connect(':memory:')) as db:
                db.execute('create table jobs(path text primary key)')
                with patch.object(pathlib.Path,'lstat',deny_locked_file):
                    pending=service.pending_recordings(base,root,dt.datetime(2026,11,7),0,db)
                self.assertEqual([file for _,file in pending],[available])
                pending=service.pending_recordings(base,root,dt.datetime(2026,11,7),0,db)
                self.assertEqual([file for _,file in pending],[locked,available])
    def test_real_video_static_motion_timestamps_and_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);source=root/'source.mp4';dest=root/'optimized.mp4'
            reply=service.run(['-v','error','-y','-f','lavfi','-i','color=c=gray:s=640x360:r=15:d=10','-f','lavfi','-i','testsrc2=s=640x360:r=15:d=5','-f','lavfi','-i','color=c=gray:s=640x360:r=15:d=25','-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=40','-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]','-map','[v]','-map','3:a','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',source])
            self.assertEqual(reply.returncode,0,reply.stderr.decode(errors='replace'))
            info=service.probe(source);windows=service.motion_windows(source,info['duration'],self.settings)
            self.assertTrue(any(a<=10 and b>=24 for a,b in windows),windows)
            self.assertFalse(any(a<=30<=b for a,b in windows),windows)
            before,after,kept=service.optimize(source,dest,windows,self.settings)
            self.assertTrue(after['audio']);self.assertLess(abs(after['duration']-before['duration']),.35)
            decoded=service.run(['-i',dest,'-an','-vf','showinfo','-f','null','-'])
            times=[float(v) for v in re.findall(rb'pts_time:([\d.]+)',decoded.stderr)]
            self.assertGreater(len([t for t in times if 11<t<14]),35)
            idle=[t for t in times if 28<t<34]
            self.assertGreater(len(idle),5);self.assertLess(len(idle),16)
            self.assertGreater(times[-1],39.8)
            (service.ROOT/'test-results.json').write_text(json.dumps({'input_bytes':source.stat().st_size,'output_bytes':dest.stat().st_size,'input_duration':before['duration'],'output_duration':after['duration'],'input_frames':600,'output_frames':len(times),'active_range_frames_3s':len([t for t in times if 11<t<14]),'idle_range_frames_6s':len(idle),'audio_preserved':after['audio'],'windows':kept},indent=2))

if __name__=='__main__':unittest.main()
