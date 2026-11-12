"""Destructive cases use temporary recordings and a temporary SQLite database only."""
import contextlib,json,pathlib,sqlite3,tempfile,unittest
import service
from camera_registry import Registry,channel_lock
import relay_manager

class DynamicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve();self.source=self.root/'data.db';self.records=self.root/'records';self.records.mkdir()
        with contextlib.closing(sqlite3.connect(self.source)) as db:
            db.executescript('create table channels(id text,name text,device_id text,enabled integer,custom_name text);create table devices(id text,url text);create table events(id integer primary key,channel_id text,start_time text,duration integer,snapshot_path text,event_type text);create table record_files(id integer primary key,channel_id text,path text);');db.commit()
        self.app=service.App(self.root/'app',self.source,self.records)
    def tearDown(self):self.temp.cleanup()
    def add(self,index):
        cid=f'camera{index}_01';folder=self.records/f'camera{index}'/'01'/'20261106';folder.mkdir(parents=True);video=folder/'20261106100000-60000.mp4';video.write_bytes(b'recording');image=folder/'event.jpg';image.write_bytes(b'image')
        with contextlib.closing(sqlite3.connect(self.source)) as db:
            db.execute('insert into channels values(?,?,?,?,?)',(cid,f'camera {index}',f'camera{index}',1,None));db.execute('insert into devices values(?,?)',(f'camera{index}',f'http://127.0.0.1:{19000+index}/live.m3u8'))
            db.execute('insert into events values(?,?,?,?,?,?)',(index,cid,'2026-11-06 10:00:03',5000,str(image.relative_to(self.records)),'1'));db.execute('insert into record_files values(?,?,?)',(index,cid,str(video)));db.commit()
        return cid,video,image
    def run_cleanup(self,cid,mode):
        plan=self.app.prepare_cleanup(cid,mode);return self.app.cleanup(plan['token'],cid,plan['name'])
    def test_active_recording_does_not_keep_deleted_snapshot_events(self):
        cid,video,image=self.add(1);plan=self.app.prepare_cleanup(cid,'all')
        video.write_bytes(b'active recording changed since confirmation')
        result=self.app.cleanup(plan['token'],cid,plan['name'])
        self.assertFalse(result['complete']);self.assertEqual(result['skipped'],1)
        self.assertTrue(video.exists());self.assertFalse(image.exists());self.assertEqual(result['events'],1)
        self.assertEqual(self.app.payload()['events'],[])
    def test_zero_one_five_and_live_add_remove(self):
        self.assertEqual(self.app.payload()['cameras'],[]);self.assertEqual(self.app.storage()['cameras'],[])
        cid,_,_=self.add(1);self.assertEqual(len(self.app.payload()['cameras']),1)
        for i in range(2,6):self.add(i)
        self.assertEqual(len(self.app.payload()['cameras']),5);self.assertEqual(len(self.app.storage()['cameras']),5)
        self.add(6);self.add(7);self.assertEqual(len(self.app.storage()['cameras']),7)
        with contextlib.closing(sqlite3.connect(self.source)) as db:db.execute('delete from channels where id=?',(cid,));db.commit()
        cameras=self.app.payload()['cameras'];self.assertEqual(len(cameras),7);self.assertTrue(next(c for c in cameras if c['channel_id']==cid)['archived'])
        self.assertEqual(len(Registry(self.source,self.records).list(history=False)),6)
    def test_keep_events_preserves_playback_and_deletes_unrelated_recording(self):
        cid,video,image=self.add(1);other,other_video,other_image=self.add(2)
        unrelated=video.with_name('20261106090000-60000.mp4');unrelated.write_bytes(b'no event')
        with contextlib.closing(sqlite3.connect(self.source)) as db,db:
            db.execute('insert into record_files values(?,?,?)',(3,cid,str(unrelated.relative_to(self.records))))
        result=self.run_cleanup(cid,'videos')
        self.assertTrue(result['complete']);self.assertEqual(result['playable_events'],1)
        self.assertFalse(unrelated.exists());self.assertTrue(image.exists());self.assertTrue(other_video.exists());self.assertTrue(other_image.exists())
        row=self.app.row(1);self.assertFalse(row['video_cleared']);self.assertTrue(row['has_image'])
        self.assertEqual(self.app.video_info(row)['offset'],3)
        with self.app.event_video(row) as file:self.assertEqual(file.read_bytes(),b'recording')
        with self.app.db() as db:self.assertEqual([r[0] for r in db.execute('select id from record_files order by id')],[1,2])
    def test_new_cross_midnight_event_protects_both_parts_after_confirmation(self):
        cid,video,image=self.add(1)
        previous=video.parent.parent/'20261105'/'20261105235850-90000.mp4'
        previous.parent.mkdir();previous.write_bytes(b'first part')
        after=video.with_name('20261106000020-60000.mp4');after.write_bytes(b'second part')
        plan=self.app.prepare_cleanup(cid,'videos');self.assertEqual(plan['files'],2)
        with contextlib.closing(sqlite3.connect(self.source)) as db,db:
            db.execute('insert into events values(?,?,?,?,?,?)',(9,cid,'2026-11-06 00:00:10',20000,str(image.relative_to(self.records)),'1'))
        result=self.app.cleanup(plan['token'],cid,plan['name'])
        self.assertEqual(result['protected_videos'],3)
        self.assertEqual([c['path'] for c in self.app.clips(self.app.row(9))],[previous,after])
    def test_global_modes_and_channel_configuration_preserved(self):
        files=[self.add(i) for i in range(1,6)];result=self.run_cleanup('all','videos');self.assertEqual(result['kept_events'],5)
        for _,video,image in files:self.assertTrue(video.exists());self.assertTrue(image.exists())
        result=self.run_cleanup('all','all');self.assertEqual(result['events'],5)
        for _,video,image in files:self.assertFalse(video.exists());self.assertFalse(image.exists())
        self.assertEqual(len(self.app.payload()['cameras']),5);self.assertEqual(self.app.payload()['events'],[])
    def test_channel_change_invalidates_confirmation(self):
        cid,video,_=self.add(1);plan=self.app.prepare_cleanup('all');self.add(2)
        with self.assertRaisesRegex(ValueError,'配置已变化'):self.app.cleanup(plan['token'],'all',plan['name'])
        self.assertTrue(video.exists())
    def test_archived_channel_remains_scoped_and_clearable(self):
        cid,video,image=self.add(1);_,other,_=self.add(2)
        with contextlib.closing(sqlite3.connect(self.source)) as db:db.execute('delete from channels where id=?',(cid,));db.commit()
        result=self.run_cleanup(cid,'all');self.assertEqual(result['events'],1);self.assertFalse(video.exists());self.assertFalse(image.exists());self.assertTrue(other.exists())
        self.assertNotIn(cid,{r['channel_id'] for r in self.app.payload()['cameras']})
    def test_cross_process_lock_denies_second_writer(self):
        lockroot=self.root/'locks'
        with channel_lock('camera1_01',lockroot):
            with self.assertRaisesRegex(ValueError,'正在维护'):
                with channel_lock('camera1_01',lockroot,timeout=.1):pass
    def test_relay_selection_tracks_enabled_local_ports(self):
        rows=[{'key':f'adapter{i}','port':19000+i} for i in range(1,8)]
        for i in range(1,6):self.add(i)
        cams=Registry(self.source,self.records).list(history=False,private=True)
        self.assertEqual(len(relay_manager.desired(rows,cams)),5);cams[0]['enabled']=False
        self.assertEqual(len(relay_manager.desired(rows,cams)),4);self.assertEqual(relay_manager.desired(rows,[]),{})
    def test_device_rename_and_channel_override_follow_every_surface(self):
        cid,_,_=self.add(1)
        with contextlib.closing(sqlite3.connect(self.source)) as db:
            db.execute('alter table devices add column name text');db.execute('update devices set name=?',('设备列表新名字',));db.commit()
        self.assertEqual(self.app.payload()['cameras'][0]['name'],'设备列表新名字')
        self.assertEqual(self.app.row(1)['name'],'设备列表新名字')
        self.assertEqual(self.app.storage()['cameras'][0]['name'],'设备列表新名字')
        self.assertEqual(self.app.prepare_cleanup(cid)['name'],'设备列表新名字')
        with contextlib.closing(sqlite3.connect(self.source)) as db:db.execute('update channels set custom_name=?',('通道自定义名字',));db.commit()
        self.assertEqual(self.app.payload()['events'][0]['name'],'通道自定义名字')
        self.assertEqual(self.app.storage()['cameras'][0]['name'],'通道自定义名字')

if __name__=='__main__':unittest.main()
