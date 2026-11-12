import contextlib,http.client,json,pathlib,sqlite3,tempfile,threading,time,unittest
from unittest.mock import patch
import service
TEST_CAMERAS=[('testFront_01','正面测试'),('testSide_01','侧面测试'),('testYi_01','空白测试')]

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve();self.source=self.root/'source.db';self.records=self.root/'records';self.records.mkdir();(self.records/'event.jpg').write_bytes(b'preview-image')
        with contextlib.closing(sqlite3.connect(self.source)) as db:
            db.execute('create table channels(id text,name text)');db.execute('create table events(id integer,channel_id text,start_time text,duration integer,snapshot_path text,event_type text)')
            for i,(channel,name) in enumerate(TEST_CAMERAS):
                db.execute('insert into channels values(?,?)',(channel,name))
                if i<2:db.execute('insert into events(id,channel_id,start_time,duration,snapshot_path) values(?,?,?,?,?)',(i+1,channel,'2026-11-05 13:00:00+08:00',10000,'event.jpg'))
            db.commit()
        self.app=service.App(self.root/'app',self.source,self.records)
        device,channel=TEST_CAMERAS[0][0].rsplit('_',1)
        folder=self.records/device/channel/'20261105';folder.mkdir(parents=True)
        self.video_file=folder/'20261105125958-14000.mp4';self.video_file.write_bytes(b'0123456789')
        (folder/'20261105120000-1000.mp4').write_bytes(b'unrelated')
    def tearDown(self):self.temp.cleanup()
    def test_three_cameras_present_including_empty_camera(self):
        data=self.app.payload();self.assertEqual(len(data['cameras']),3);self.assertEqual([r['total'] for r in data['cameras']],[1,1,0]);self.assertEqual(len(data['events']),2)
        self.assertNotIn('analysis',data['events'][0]);self.assertNotIn('status',data['events'][0])
    def test_source_read_only_and_snapshot_paths_scoped(self):
        with self.app.db() as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute('delete from events')
        row=self.app.row(1);row['snapshot_path']='../outside.jpg'
        with self.assertRaises(ValueError):self.app.snapshot(row)
        with self.assertRaises(ValueError):self.app.row(999)
    def test_recording_matches_camera_and_overlapping_time(self):
        clips=self.app.clips(self.app.row(1));self.assertEqual([item['path'] for item in clips],[self.video_file])
        self.assertEqual(self.app.video(self.app.row(1)),self.video_file)
        self.assertEqual(self.app.video_info(self.app.row(1))['duration'],14)
        with self.assertRaisesRegex(ValueError,'本地录像'):self.app.video_info(self.app.row(2))
        next_part=self.video_file.parent/'20261105130008-6000.mp4';next_part.write_bytes(b'next part')
        self.assertEqual([item['path'] for item in self.app.clips(self.app.row(1))],[self.video_file,next_part])
    def test_old_model_routes_removed_and_images_preserved(self):
        self.app.thumbnail=self.app.snapshot
        server=service.ThreadingHTTPServer(('127.0.0.1',0),service.handler(self.app));worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            last_headers={}
            def request(method,path,extra=None):
                client.request(method,path,headers={'Host':'127.0.0.1:18768',**(extra or {})});response=client.getresponse();last_headers.clear();last_headers.update(dict(response.getheaders()));return response.status,response.read()
            self.assertEqual(request('GET','/api/config')[0],404)
            self.assertEqual(request('POST','/api/events/1/analyze')[0],405)
            self.assertEqual(request('GET','/api/events/1/image'),(200,b'preview-image'))
            code,data=request('GET','/api/events');self.assertEqual(code,200);self.assertEqual(len(json.loads(data)['cameras']),3)
            self.assertEqual(request('GET','/api/events/1/video',{'Range':'bytes=2-5'}),(206,b'2345'))
            self.assertEqual(request('GET','/api/events/1/video',{'Range':'bytes=-3'}),(206,b'789'))
            self.assertEqual(request('GET','/api/events/1/video',{'Range':'bytes=50-60'})[0],416)
            self.assertEqual(request('HEAD','/api/events/1/video'),(200,b''))
            self.assertEqual(request('GET','/api/events/2/video-info')[0],404)
            self.assertEqual(request('GET','/api/events/1/download'),(200,b'0123456789'))
            self.assertIn('attachment;',last_headers['Content-Disposition']);self.assertIn("filename*=UTF-8''",last_headers['Content-Disposition']);self.assertIn('20261105_130000.mp4',last_headers['Content-Disposition'])
            self.assertEqual(request('GET','/api/events/1/download',{'Range':'bytes=0-3'}),(206,b'0123'))
            client.close()
        finally:server.shutdown();server.server_close();worker.join()

class StorageTests(PreviewTests):
    def setUp(self):
        super().setUp()
        device,channel=TEST_CAMERAS[1][0].rsplit('_',1)
        self.other=self.records/device/channel/'20261105'/'side.mp4';self.other.parent.mkdir(parents=True);self.other.write_bytes(b'keep side camera')
        self.front_cache=self.app.cache/'1-preview.jpg';self.front_cache.write_bytes(b'front cache')
        self.side_cache=self.app.cache/'2-preview.jpg';self.side_cache.write_bytes(b'side cache')
        with contextlib.closing(sqlite3.connect(self.source)) as db:
            db.execute('create table record_files(id integer,channel_id text)')
            db.executemany('insert into record_files values(?,?)',[(1,TEST_CAMERAS[0][0]),(2,TEST_CAMERAS[1][0])])
            db.commit()
    def test_usage_and_camera_scoped_cleanup(self):
        data=self.app.storage();self.assertGreater(data['disk']['total'],0);self.assertLessEqual(data['disk']['free'],data['disk']['total']);self.assertEqual(data['cameras'][0]['videos'],2)
        channel=TEST_CAMERAS[0][0];plan=self.app.prepare_cleanup(channel);result=self.app.cleanup(plan['token'],channel,plan['name'])
        self.assertTrue(result['complete']);self.assertEqual(result['events'],1);self.assertFalse(self.video_file.exists());self.assertFalse(self.front_cache.exists());self.assertTrue(self.other.exists());self.assertTrue(self.side_cache.exists());self.assertTrue((self.records/'event.jpg').exists())
        with self.app.db() as db:
            self.assertEqual(db.execute('select channel_id from events').fetchall()[0][0],TEST_CAMERAS[1][0]);self.assertEqual(db.execute('select count(*) from channels').fetchone()[0],3);self.assertEqual(db.execute('select id from record_files').fetchall()[0][0],2)
        with self.assertRaises(ValueError):self.app.cleanup(plan['token'],channel,plan['name'])
    def test_wrong_confirmation_expiry_and_unknown_camera_do_not_delete(self):
        channel=TEST_CAMERAS[0][0];plan=self.app.prepare_cleanup(channel)
        with self.assertRaises(ValueError):self.app.cleanup(plan['token'],channel,'错误名称')
        self.assertTrue(self.video_file.exists())
        plan=self.app.prepare_cleanup(channel);self.app.cleanup_plans[plan['token']]['at']=time.monotonic()-301
        with self.assertRaises(ValueError):self.app.cleanup(plan['token'],channel,plan['name'])
        with self.assertRaises(ValueError):self.app.prepare_cleanup('../../other')
        self.assertTrue(self.video_file.exists())
    def test_changed_file_and_outside_path_preserved(self):
        channel=TEST_CAMERAS[0][0];plan=self.app.prepare_cleanup(channel)
        self.video_file.write_bytes(b'active recording grew')
        outside=self.root/'outside.mp4';outside.write_bytes(b'keep outside')
        info=outside.stat();self.app.cleanup_plans[plan['token']]['files'].append({'path':str(outside),'size':info.st_size,'mtime':info.st_mtime_ns})
        result=self.app.cleanup(plan['token'],channel,plan['name'])
        self.assertEqual(result['skipped'],2);self.assertFalse(result['complete']);self.assertTrue(self.video_file.exists());self.assertTrue(outside.exists());self.assertEqual(self.app.row(1)['event_id'],1)
    def test_preview_cache_cleanup_keeps_recordings_events_and_active_video(self):
        cached_video=self.app.cache/'1-video-0123456789abcdef.mp4'
        cached_video.write_bytes(b'cached event video')
        unrelated=self.app.cache/'notes.txt'
        unrelated.write_bytes(b'keep unrelated file')
        with patch.object(self.app,'video',return_value=cached_video):
            with self.app.event_video(self.app.row(1)):
                result=self.app.clear_preview_cache()
                self.assertEqual(result,{'removed':2,'released':21,'skipped':1})
                self.assertTrue(cached_video.is_file())
        self.assertEqual(self.app.clear_preview_cache()['removed'],1)
        self.assertTrue(unrelated.is_file())
        self.assertTrue(self.video_file.is_file())
        self.assertTrue(self.other.is_file())
        self.assertTrue(self.app.snapshot(self.app.row(1)).is_file())
        with self.app.db() as db:
            self.assertEqual(db.execute('select count(*) from events').fetchone()[0],2)
            self.assertEqual(db.execute('select count(*) from record_files').fetchone()[0],2)
    def test_http_confirmation_and_cross_origin_guards(self):
        server=service.ThreadingHTTPServer(('127.0.0.1',0),service.handler(self.app));worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            def post(route,body,extra):
                client.request('POST','/api/storage/'+route,body=json.dumps(body),headers={'Host':'127.0.0.1:18768','Content-Type':'application/json',**extra});response=client.getresponse();return response.status,json.loads(response.read())
            channel=TEST_CAMERAS[0][0]
            self.assertEqual(post('prepare-cleanup',{'channel_id':channel},{})[0],403)
            headers={'Origin':'http://127.0.0.1:18768','X-CSRF-Token':self.app.csrf}
            self.assertEqual(post('prepare-cleanup',{'channel_id':channel},{**headers,'Origin':'http://attacker.invalid'})[0],403)
            self.assertEqual(post('clear-preview-cache',{},{} )[0],403)
            self.assertTrue(self.front_cache.is_file())
            self.assertEqual(post('clear-preview-cache',{},headers)[0],200)
            self.assertFalse(self.front_cache.exists())
            self.assertTrue(self.video_file.is_file())
            status,plan=post('prepare-cleanup',{'channel_id':channel},headers);self.assertEqual(status,200);self.assertTrue(self.video_file.exists())
            status,result=post('cleanup',{'channel_id':channel,'token':plan['token'],'confirmation':plan['name']},headers);self.assertEqual(status,200);self.assertTrue(result['complete']);self.assertTrue(self.other.exists())
            self.assertEqual(post('cleanup',{'channel_id':channel,'token':plan['token'],'confirmation':plan['name']},headers)[0],400)
            client.close()
        finally:server.shutdown();server.server_close();worker.join()

class AdaptiveSettingsTests(unittest.TestCase):
    def test_switch_is_persistent_scoped_and_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);folder=root/'adaptive-recording';folder.mkdir();config=folder/'settings.json';config.write_text(json.dumps({'enabled':True,'idle_fps':2,'pre_seconds':3,'post_seconds':10,'maxrate_kbps':3000}))
            app=service.App(root/'ai-events',root/'unused.db',root/'records')
            server=service.ThreadingHTTPServer(('127.0.0.1',0),service.handler(app));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=http.client.HTTPConnection('127.0.0.1',server.server_port)
                def request(method,body=None,extra=None):
                    client.request(method,'/api/adaptive-recording',body=json.dumps(body) if body is not None else None,headers={'Host':'127.0.0.1:18768','Content-Type':'application/json',**(extra or {})});response=client.getresponse();return response.status,json.loads(response.read())
                code,data=request('GET');self.assertEqual(code,200);self.assertTrue(data['enabled']);self.assertFalse(data['healthy'])
                self.assertEqual(request('POST',{'enabled':False})[0],403);self.assertTrue(json.loads(config.read_text())['enabled'])
                headers={'Origin':'http://127.0.0.1:18768','X-CSRF-Token':app.csrf}
                self.assertEqual(request('POST',{'enabled':False},{**headers,'Origin':'http://example.invalid'})[0],403)
                self.assertEqual(request('POST',{'enabled':'false'},headers)[0],400)
                self.assertEqual(request('POST',{'enabled':False},headers)[0],200);self.assertFalse(json.loads(config.read_text())['enabled'])
                self.assertEqual(request('POST',{'enabled':True},headers)[0],200);self.assertTrue((folder/'activation.json').is_file());self.assertEqual(json.loads(config.read_text())['maxrate_kbps'],3000)
                client.close()
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
