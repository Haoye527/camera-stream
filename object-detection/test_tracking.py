import contextlib,datetime as dt,pathlib,sqlite3,tempfile,unittest
from PIL import Image
from tracking import PersonPipeline,settings,region
from detector import Events

def human(score=.8,x=100):return {'class':0,'label':'person','type':'1','confidence':score,'box':[x,100,x+50,220]}
class FakeModel:
    def __init__(self):self.hits=[]
    def infer(self,image,threshold):return [dict(h) for h in self.hits if h['confidence']>=threshold]

class TrackingTests(unittest.TestCase):
    def make(self):
        m=FakeModel();c={**settings(),'roi_enabled':False};p=PersonPipeline(m,c);return m,p
    def test_low_confidence_cannot_create_track_but_can_continue_confirmed_person(self):
        m,p=self.make();im=Image.new('RGB',(1280,720));t=dt.datetime(2026,11,7,9).timestamp()
        m.hits=[human(.25)]
        for i in range(4):self.assertEqual(p.process(im,t+i),[])
        m.hits=[human()];self.assertFalse(p.process(im,t+5)[0]['track_confirmed'])
        found=p.process(im,t+5.5);self.assertEqual(len(found),1);identity=found[0]['track_id']
        m.hits=[human(.25,x=102)];found=p.process(im,t+6)
        self.assertEqual(len(found),1);self.assertEqual(found[0]['track_id'],identity);self.assertLess(found[0]['confidence'],.5)
        self.assertTrue(found[0]['track_confirmed'])
    def test_gaps_have_no_fabricated_boxes_and_low_score_after_timeout_cannot_resurrect(self):
        m,p=self.make();im=Image.new('RGB',(1280,720));t=1000
        m.hits=[human()];p.process(im,t);self.assertEqual(len(p.process(im,t+.5)),1)
        m.hits=[];self.assertEqual(p.process(im,t+1),[])
        m.hits=[human(.25)];self.assertEqual(p.process(im,t+8),[])
    def test_camera_instances_are_isolated_and_roi_is_scoped(self):
        m,p=self.make();m2,p2=self.make();im=Image.new('RGB',(1280,720));m.hits=[human()]
        p.process(im,1000);self.assertEqual(len(p.process(im,1001)),1);self.assertEqual(p2.process(im,1001),[])
        for box in ([0,0,30,60],[1240,650,1280,720]):
            x,y,r,b=region(box,1280,720);self.assertTrue(0<=x<r<=1280 and 0<=y<b<=720)
    def test_disabled_human_resets_track(self):
        m,p=self.make();m.hits=[human()];im=Image.new('RGB',(1280,720));p.process(im,1000);p.process(im,1001)
        self.assertEqual(p.process(im,1002,False),[]);self.assertFalse(p.active(1002))

    def test_bear_requires_local_confirmation_without_dropping_separate_dog(self):
        image=Image.new('RGB',(1280,720))
        bear={'class':21,'label':'bear','type':'3','confidence':.73,'box':[1157,600,1242,719]}
        dog={'class':16,'label':'dog','type':'3','confidence':.91,'box':[350,420,420,490]}
        crop=region(bear['box'],image.width,image.height)
        local_box=[coordinate-crop[index%2] for index,coordinate in enumerate(bear['box'])]
        model=FakeModel()
        local_hits=[{**human(.8),'box':local_box}]
        model.infer=lambda frame,threshold: [bear,dog] if frame.size==image.size else local_hits
        pipeline=PersonPipeline(model)
        self.assertEqual({h['label'] for h in pipeline.process(image,1000)}, {'dog','person'})
        self.assertEqual({h['label'] for h in pipeline.process(image,1001,False)}, {'dog'})
        local_hits[:]=[{**bear,'box':local_box}]
        self.assertEqual({h['label'] for h in pipeline.process(image,1002,False)}, {'dog','bear'})

    def test_recent_track_blocks_only_overlapping_animal(self):
        model,pipeline=self.make()
        image=Image.new('RGB',(1280,720))
        model.hits=[human()]
        pipeline.process(image,1000)
        self.assertTrue(pipeline.process(image,1000.5)[0]['track_confirmed'])
        dog={'class':16,'label':'dog','type':'3','confidence':.75,'box':human()['box']}
        model.hits=[dog,{**dog,'box':[300,250,340,295]}]
        self.assertEqual([h['box'] for h in pipeline.process(image,1001)],[[300,250,340,295]])
        self.assertEqual(len(pipeline.process(image,1004)),2)

    def test_confirmed_track_creates_event_without_second_confirmation_delay(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);dbpath=root/'events.db'
            with contextlib.closing(sqlite3.connect(dbpath)) as db:db.execute('create table events(id integer primary key,channel_id,event_type,start_time,trigger_time,last_trigger_time,duration,snapshot_path,description,created_at,updated_at)')
            model,p=self.make();model.hits=[human()];im=Image.new('RGB',(1280,720));now=dt.datetime(2026,11,7,9,tzinfo=dt.timezone(dt.timedelta(hours=8)))
            writer=Events(dbpath,root/'records',source='local-yolo26s',grace_seconds=5)
            for second in (0,.5):
                instant=now+dt.timedelta(seconds=second);writer.update('camera_01',{'1'},p.process(im,instant.timestamp()),im,instant)
            with contextlib.closing(sqlite3.connect(dbpath)) as db:self.assertEqual(db.execute('select count(*),start_time from events').fetchone(),(1,now.isoformat(' ')))

if __name__=='__main__':unittest.main()
