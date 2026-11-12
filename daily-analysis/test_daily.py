import contextlib,datetime as dt,json,pathlib,sqlite3,tempfile,unittest
from unittest.mock import patch
import daily_analysis as daily
import evidence,vision

class FakeApp:
    csrf='test-token'
    def __init__(self,folder):
        self.source=folder/'events.db'
        with contextlib.closing(sqlite3.connect(self.source)) as db:
            db.execute('CREATE TABLE events(id INTEGER,channel_id TEXT,start_time TEXT,duration INTEGER,event_type TEXT)')
            db.executemany('INSERT INTO events VALUES(?,?,?,?,?)',[(1,'camera-a','2026-11-06 09:00:00',7000,'1'),(2,'camera-a','2026-11-06 09:00:15',4000,'1'),(3,'camera-b','2026-11-06 10:00:00',2000,'1')]);db.commit()
    def cameras(self):return [{'channel_id':k,'name':k} for k in ('camera-a','camera-b')]
    @contextlib.contextmanager
    def db(self):
        with contextlib.closing(sqlite3.connect(self.source)) as db:db.row_factory=sqlite3.Row;yield db

class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name);self.app=FakeApp(self.root);self.service=daily.DailyService(self.app,'ffmpeg',self.root/'daily',start=False)
    def tearDown(self):self.temp.cleanup()
    def test_grouping_and_boundaries(self):
        rows=[{'id':1,'channel_id':'a','start_time':'2026-11-06 09:00:00','duration':7000},{'id':2,'channel_id':'a','start_time':'2026-11-06 09:00:15','duration':4000},{'id':3,'channel_id':'b','start_time':'2026-11-06 09:00:17','duration':200000}]
        segments=evidence.candidates(rows,{'a':'院子','b':'门口'})
        self.assertEqual(segments[0]['event_ids'],[1,2]);self.assertEqual(segments[0]['location'],'院子');self.assertEqual(len(segments),3)
        self.assertTrue(all((s['end']-s['start']).total_seconds()<=120 for s in segments))
    def test_validation_rejects_unsupported_evidence(self):
        with self.assertRaises(ValueError):vision.validate({'sentence':'走动','actions':['走动'],'evidence_frames':[99]},8)
        with self.assertRaises(ValueError):vision.validate({'sentence':'走动','actions':['确定摔倒'],'evidence_frames':[1]},8)
    def test_dpapi_and_no_secret_in_bootstrap(self):
        self.service.settings({'locations':{'camera-a':'正门'},'automatic':False,'daily_limit':20,'max_segments':10,'api_key':'dummy-local-test-key'})
        self.assertEqual(self.service.key(),'dummy-local-test-key');self.assertNotIn('dummy-local-test-key',self.service.configfile.read_text())
        self.assertNotIn('dummy-local-test-key',json.dumps(self.service.bootstrap()));self.assertTrue(self.service.bootstrap()['has_key'])
    def test_job_scoping_cache_and_partial_failure(self):
        self.service.settings({'locations':{},'automatic':False,'daily_limit':20,'max_segments':10,'api_key':'dummy-local-test-key'})
        def extract(segment,clips,folder,ffmpeg,**kwargs):
            if segment['channel_id']=='camera-b':raise ValueError('录像已清理')
            folder.mkdir(parents=True,exist_ok=True);p=folder/'000.jpg';p.write_bytes(b'test-jpeg')
            return [{'time':segment['start'].isoformat(' '),'path':p},{'time':segment['end'].isoformat(' '),'path':p}]
        answer={'sentence':'一人在院内走动。','actions':['走动'],'evidence_frames':[1],'uncertainty':''}
        summary={'one_line':'观察到院内有人走动。','highlights':[]}
        with patch.object(evidence,'index',return_value=[]),patch.object(evidence,'extract',side_effect=extract),patch.object(vision,'analyze',return_value=(answer,{})) as call,patch.object(vision,'summarize',return_value=(summary,{})):
            j=self.service.create({'date':'2026-11-06'});self.service.run(j);first=self.service.job(j['id'])
            self.assertEqual(first['status'],'partial');self.assertEqual(len(first['results']),1);self.assertEqual(len(first['failures']),1)
            self.service.run(self.service.create({'date':'2026-11-06','channel_id':'camera-a'}));self.assertEqual(call.call_count,1)
    def test_budget_and_cancel(self):
        self.service.settings({'locations':{},'automatic':False,'daily_limit':2,'max_segments':10,'api_key':'dummy-local-test-key'})
        self.service.budget();self.service.budget()
        with self.assertRaises(ValueError):self.service.budget()
        j=self.service.create({'date':'2026-11-06'});self.service.cancel(j['id']);j['status']='complete';self.assertFalse(self.service.publish(j));self.assertEqual(self.service.job(j['id'])['status'],'cancelled')
    def test_image_path_traversal(self):
        for path in ('../settings.private.json','a'*64+'/../../secret.jpg','a'*64+'/000.jpg.exe'):
            with self.assertRaises(ValueError):self.service.image(path)
    def test_summary_rejects_invented_references(self):
        with patch.object(vision,'request',return_value=({'one_line':'测试','highlights':[{'text':'测试','evidence_ids':['invented']}]},{})):
            with self.assertRaises(ValueError):vision.summarize('dummy',[{'id':'actual','start':'09:00','location':'门口','sentence':'走动','uncertainty':''}])

    def test_short_event_is_included_in_sparse_sampling(self):
        segment=evidence.candidates([{'id':1,'channel_id':'a','start_time':'2026-11-06 09:00:00','duration':2000}],{'a':'院子'})[0]
        clip={'start':segment['start'],'end':segment['end'],'path':self.root/'source.mp4'}
        def decode(command,**kwargs):
            pathlib.Path(command[-1]).write_bytes(b'jpeg')
            return type('Result',(),{'returncode':0})()
        with patch.object(evidence.subprocess,'run',side_effect=decode):frames=evidence.extract(segment,[clip],self.root/'frames','ffmpeg')
        in_event=[f for f in frames if '09:00:00'<=f['time'][11:]<'09:00:02']
        self.assertGreaterEqual(len(in_event),2);self.assertLessEqual(len(frames),8)

    def test_vision_request_contains_only_user_images_and_real_timestamps(self):
        p=self.root/'test.jpg';p.write_bytes(b'jpeg')
        response={'sentence':'一人在走动。','actions':['走动'],'evidence_frames':[1],'uncertainty':''}
        with patch.object(vision,'request',return_value=(response,{})) as call:
            vision.analyze('secret','院子',[{'time':'2026-11-06 09:00:00','path':p}])
        messages=call.call_args.args[1]
        self.assertIsInstance(messages[0]['content'],str)
        self.assertEqual(messages[1]['role'],'user')
        self.assertIn('2026-11-06 09:00:00',json.dumps(messages,ensure_ascii=False))
        self.assertTrue(messages[1]['content'][-1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

if __name__=='__main__':unittest.main()
