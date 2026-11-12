import contextlib,datetime as dt,json,pathlib,sqlite3,tempfile,unittest
import numpy as np
from PIL import Image
from detector import Events,Yolo26Model,capture_command,group,nms,plan_types

class DetectorTests(unittest.TestCase):
    def test_hls_starts_near_live_edge_without_breaking_rtsp(self):
        command=capture_command('http://127.0.0.1:18765/live.m3u8?test=1',False)
        self.assertEqual(command[command.index('-live_start_index')+1],'-1')
        self.assertIn('fps=1,scale=640:360',command)
        self.assertLess(command.index('-live_start_index'),command.index('-i'))
        self.assertNotIn('-live_start_index',capture_command('rtsp://127.0.0.1/live'))
    def test_yolo26_letterbox_normalization_and_coordinate_restore(self):
        image=Image.new('RGB',(1280,720),(255,0,0))
        tensor,ratio,left,top=Yolo26Model.preprocess(image)
        self.assertEqual(tensor.shape,(1,3,640,640));self.assertEqual((ratio,left,top),(.5,0,140))
        np.testing.assert_allclose(tensor[0,:,200,200],[1,0,0])
        np.testing.assert_allclose(tensor[0,:,0,0],[114/255]*3)
        raw=np.array([[50,165,100,240,.9,0],[0,140,640,500,.8,16],[10,160,20,170,.2,2],
                      [10,160,20,170,.9,10],[0,0,10,10,.9,0],[0,140,640,500,np.nan,0]],dtype=np.float32)
        hits=Yolo26Model.decode(raw,image,ratio,left,top,.5)
        self.assertEqual([h['type'] for h in hits],['1','3'])
        np.testing.assert_allclose(hits[0]['box'],[100,50,200,200])
        np.testing.assert_allclose(hits[1]['box'],[0,0,1280,720])
    def test_categories_and_overlapping_boxes(self):
        self.assertEqual([group(n) for n in (0,2,16,10)],['1','2','3',None])
        self.assertEqual(nms(np.array([[0,0,20,20],[1,1,21,21],[30,30,40,40]]),np.array([.9,.8,.7])),[0,2])
    def test_native_plan_controls_schedule_and_categories(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('create table record_plans(id,enabled,plans,event_types)');db.execute('create table record_with_channels(channel_id,plan_id)')
            schedule='0'*5+'1'*15+'0'*4
            db.execute('insert into record_plans values(1,1,?,?)',(schedule*7,json.dumps([{'type':str(n)} for n in range(4)])))
            db.execute("insert into record_with_channels values('test_01',1)")
            self.assertEqual(plan_types(db,'test_01',dt.datetime(2026,11,5,6)),{'1','2','3'})
            self.assertEqual(plan_types(db,'test_01',dt.datetime(2026,11,5,20)),set())
            db.execute('update record_plans set enabled=0');self.assertEqual(plan_types(db,'test_01',dt.datetime(2026,11,5,6)),set())
    def test_debounce_separate_types_disabled_and_recorded_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);dbpath=root/'test.db'
            with contextlib.closing(sqlite3.connect(dbpath)) as db:db.execute('create table events(id integer primary key,channel_id,event_type,start_time,trigger_time,last_trigger_time,duration,snapshot_path,description,created_at,updated_at)');db.commit()
            writer=Events(dbpath,root/'records',source='local-yolo26s');now=dt.datetime(2026,11,5,21,tzinfo=dt.timezone(dt.timedelta(hours=8)));image=Image.new('RGB',(64,36))
            hits=[{'type':kind,'label':'test','confidence':.9,'box':[1,1,20,20]} for kind in ('1','3')]
            writer.update('test_01',{'1'},hits,image,now)
            with contextlib.closing(sqlite3.connect(dbpath)) as db:self.assertEqual(db.execute('select count(*) from events').fetchone()[0],0)
            writer.update('test_01',{'1'},hits,image,now+dt.timedelta(seconds=2))
            writer.update('test_01',{'1'},hits,image,now+dt.timedelta(seconds=4))
            with contextlib.closing(sqlite3.connect(dbpath)) as db:
                rows=db.execute('select event_type,duration,snapshot_path from events').fetchall();self.assertEqual(len(rows),1);self.assertEqual(rows[0][:2],('1',4000))
                self.assertEqual(json.loads(db.execute('select description from events').fetchone()[0])['source'],'local-yolo26s')
                with Image.open(root/'records'/rows[0][2]) as picture:self.assertEqual(picture.format,'JPEG')
            writer.update('test_01',set(),[],None,now+dt.timedelta(seconds=5));self.assertFalse(writer.states)

    def test_missed_detection_grace_updates_duration_without_new_trigger(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);dbpath=root/'test.db'
            with contextlib.closing(sqlite3.connect(dbpath)) as db:db.execute('create table events(id integer primary key,channel_id,event_type,start_time,trigger_time,last_trigger_time,duration,snapshot_path,description,created_at,updated_at)')
            writer=Events(dbpath,root/'records');now=dt.datetime(2026,11,7,8);image=Image.new('RGB',(64,36));hits=[{'type':'1','label':'person','confidence':.8,'box':[1,1,20,20]}]
            for second in (0,1):writer.update('test_01',{'1'},hits,image,now+dt.timedelta(seconds=second))
            writer.update('test_01',{'1'},[],image,now+dt.timedelta(seconds=5))
            with contextlib.closing(sqlite3.connect(dbpath)) as db:
                row=db.execute('select duration,last_trigger_time from events').fetchone();self.assertEqual(row,(5000,(now+dt.timedelta(seconds=1)).isoformat(' ')))
            writer.update('test_01',{'1'},hits,image,now+dt.timedelta(seconds=7))
            writer.update('test_01',{'1'},[],image,now+dt.timedelta(seconds=25))
            with contextlib.closing(sqlite3.connect(dbpath)) as db:self.assertEqual(db.execute('select count(*),duration from events').fetchone(),(1,17000))
            self.assertFalse(writer.states)

    def test_no_video_frame_does_not_extend_event_or_recreate_deleted_row(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);dbpath=root/'test.db'
            with contextlib.closing(sqlite3.connect(dbpath)) as db:db.execute('create table events(id integer primary key,channel_id,event_type,start_time,trigger_time,last_trigger_time,duration,snapshot_path,description,created_at,updated_at)')
            writer=Events(dbpath,root/'records');now=dt.datetime(2026,11,7,8);image=Image.new('RGB',(64,36));hits=[{'type':'1','label':'person','confidence':.8,'box':[1,1,20,20]}]
            for second in (0,1):writer.update('test_01',{'1'},hits,image,now+dt.timedelta(seconds=second))
            writer.update('test_01',{'1'},[],None,now+dt.timedelta(seconds=5))
            with contextlib.closing(sqlite3.connect(dbpath)) as db:
                self.assertEqual(db.execute('select duration from events').fetchone()[0],1000);db.execute('delete from events');db.commit()
            writer.update('test_01',{'1'},[],image,now+dt.timedelta(seconds=6))
            self.assertFalse(writer.states)
            with contextlib.closing(sqlite3.connect(dbpath)) as db:self.assertEqual(db.execute('select count(*) from events').fetchone()[0],0)

if __name__=='__main__':unittest.main()
