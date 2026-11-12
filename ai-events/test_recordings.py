import contextlib,http.client,json,sqlite3,threading,unittest
import service
import test_service as fixtures
TEST_CAMERAS=fixtures.TEST_CAMERAS

class RecordingTests(unittest.TestCase):
    setUp=fixtures.PreviewTests.setUp
    tearDown=fixtures.PreviewTests.tearDown
    def test_index_matches_channel_and_date(self):
        value=self.app.recordings(TEST_CAMERAS[0][0],'2026-11-05')
        self.assertEqual(value['days'],['2026-11-05']);self.assertEqual(len(value['items']),2)
        self.assertEqual(value['items'][1]['start'],12*3600+59*60+58);self.assertEqual(value['items'][1]['duration'],14)
        self.assertEqual(self.app.recordings(TEST_CAMERAS[1][0])['items'],[])
        self.assertEqual(self.app.recordings(TEST_CAMERAS[0][0],'2026-11-04')['items'],[])
    def test_invalid_paths_and_unsealed_files_are_rejected(self):
        cid=TEST_CAMERAS[0][0]
        for name in ('../outside.mp4','20261105125958-0.mp4','20261305125958-100.mp4','20261105125958-14000.mp4/other'):
            with self.assertRaises(ValueError):self.app.recording_file(cid,name)
        with self.assertRaises(ValueError):self.app.recordings('../other','2026-11-05')
        with self.assertRaises(ValueError):self.app.recordings(cid,'../../')
        zero=self.video_file.parent/'20261105130000-0.mp4';zero.write_bytes(b'currently recording')
        self.assertEqual(len(self.app.recordings(cid)['items']),2)
    def test_event_offset_uses_recording_start(self):
        value=self.app.video_info(self.app.row(1));self.assertEqual(value['offset'],2);self.assertEqual(value['start_time'],'2026-11-05 12:59:58')
    def test_actual_range_download_and_source_routes(self):
        server=service.ThreadingHTTPServer(('127.0.0.1',0),service.handler(self.app));worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            def request(path,headers=None):
                client.request('GET',path,headers={'Host':'127.0.0.1:18768',**(headers or {})});r=client.getresponse();return r.status,dict(r.getheaders()),r.read()
            code,_,body=request('/api/recordings?channel_id='+TEST_CAMERAS[0][0]+'&date=2026-11-05');self.assertEqual(code,200);items=json.loads(body)['items']
            row=next(i for i in items if i['name']==self.video_file.name)
            code,headers,body=request(row['url'],{'Range':'bytes=2-5'});self.assertEqual((code,body),(206,b'2345'));self.assertEqual(headers['Content-Range'],'bytes 2-5/10')
            code,headers,body=request(row['download']);self.assertEqual(code,200);self.assertIn('attachment',headers['Content-Disposition'])
            self.assertEqual(request('/api/recordings?channel_id=unknown_01')[0],404)
            self.assertEqual(request('/api/recordings/testFront_01/../outside/video')[0],404)
            for file in ('replay.js','replay.css','artplayer.js'):self.assertEqual(request('/'+file)[0],200)
            client.close()
        finally:server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
