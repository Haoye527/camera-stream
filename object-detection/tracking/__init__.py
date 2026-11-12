"""YOLO26 + ByteTrack, high/low association and bounded native-frame ROI checks."""
import datetime as dt,json,pathlib
import numpy as np
from supervision import ByteTrack,Detections

def settings():return json.loads((pathlib.Path(__file__).parent/'settings.json').read_text(encoding='utf8'))

def overlap(a,b):
    left=max(a[0],b[0]);top=max(a[1],b[1]);right=min(a[2],b[2]);bottom=min(a[3],b[3])
    inter=max(0,right-left)*max(0,bottom-top)
    return inter/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)

def region(box,width,height):
    cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
    w=min(width,max(256,(box[2]-box[0])*4));h=min(height,max(256,(box[3]-box[1])*3))
    x=max(0,min(width-w,cx-w/2));y=max(0,min(height-h,cy-h/2))
    return (int(x),int(y),int(x+w),int(y+h))

class PersonPipeline:
    def __init__(self,model,config=None):
        self.model=model;self.config=config or settings();c=self.config
        self.tracker=ByteTrack(track_activation_threshold=c['high_threshold'],lost_track_buffer=30*c['lost_seconds'],
            minimum_matching_threshold=.8,frame_rate=c['active_fps'],minimum_consecutive_frames=c['confirmation_frames'])
        # Supervision defaults to activation+0.1; keep new-track threshold explicit.
        self.tracker.det_thresh=c['high_threshold']
        self.reset()
    def reset(self):
        self.tracker.reset();self.last_seen={};self.boxes={};self.frame_times={};self.last_roi=-1e9;self.last_candidate=-1e9;self.last_update=None;self.tile=0;self.stats={}
    def active(self,now):return now-self.last_candidate<=2 or any(0<=now-t<=self.config['lost_seconds'] for t in self.last_seen.values())
    def process(self,image,now,track_people=True):
        c=self.config
        if not track_people or (self.last_update is not None and now-self.last_update>c['lost_seconds']):
            self.reset()
        self.last_update=now
        recent={i:t for i,t in self.last_seen.items() if 0<=now-t<=c['lost_seconds']}
        for field in ('tracked_tracks','lost_tracks'):
            setattr(self.tracker,field,[t for t in getattr(self.tracker,field) if t.external_track_id not in self.last_seen or t.external_track_id in recent])
        threshold=c['low_threshold'] if track_people else c['high_threshold']
        raw=self.model.infer(image,threshold)
        people=[h for h in raw if h['type']=='1']
        others=[h for h in raw if h['type']!='1' and h['confidence']>=c['high_threshold']]
        person_boxes=[h['box'] for h in people if h['confidence']>=c['high_threshold']]
        recent_boxes=[self.boxes[identity] for identity,timestamp in recent.items() if now-timestamp<=2.5]

        def conflicts_with_person(hit):
            return hit['type']=='3' and (
                any(overlap(hit['box'],box)>=.45 for box in person_boxes)
                or any(overlap(hit['box'],box)>=.55 for box in recent_boxes)
            )

        others=[hit for hit in others if not conflicts_with_person(hit)]
        # In recordings, a bent or partially cropped person repeatedly scored as a bear.
        # Use the existing one-crop budget to verify that label on the same object.
        roi_enabled=c.get('roi_enabled',True)
        bear=None
        if roi_enabled:
            bear=max((hit for hit in others if hit['label']=='bear'),key=lambda hit:hit['confidence'],default=None)
        uncertain=sorted((h for h in people if h['confidence']<c['high_threshold']),key=lambda h:-h['confidence'])
        missing=[i for i in recent if not any(overlap(self.boxes[i],h['box'])>.2 for h in people if h['confidence']>=c['high_threshold'])]
        interval=c['roi_interval_seconds'] if (uncertain or missing) else c['idle_roi_interval_seconds']
        roi_count=0
        bear_confirmed=False
        if bear is not None or (track_people and roi_enabled and now-self.last_roi>=interval):
            box=None
            if bear is not None:
                box=bear['box']
            elif uncertain:
                box=uncertain[0]['box']
            elif missing:
                box=self.boxes[missing[0]]
            if box is not None:crop=region(box,image.width,image.height)
            else:
                w=round(image.width*.6);h=round(image.height*.67);x=(image.width-w)*(self.tile%2);y=(image.height-h)*(self.tile//2);crop=(x,y,x+w,y+h);self.tile=(self.tile+1)%4
            x,y,_,_=crop;roi_count=1;self.last_roi=now
            for hit in self.model.infer(image.crop(crop),c['low_threshold']):
                hit={**hit,'box':[hit['box'][0]+x,hit['box'][1]+y,hit['box'][2]+x,hit['box'][3]+y]}
                if hit['type']!='1':
                    if bear is not None and hit['label']=='bear' and hit['confidence']>=c['high_threshold'] and overlap(hit['box'],bear['box'])>=.45:
                        bear_confirmed=True
                    continue
                if hit['confidence']>=c['high_threshold']:
                    person_boxes.append(hit['box'])
                duplicate=next((i for i,h in enumerate(people) if overlap(h['box'],hit['box'])>.5),None)
                if duplicate is None:people.append(hit)
                elif hit['confidence']>people[duplicate]['confidence']:people[duplicate]=hit
        if bear is not None:
            others=[hit for hit in others if hit['label']!='bear' or (hit is bear and bear_confirmed)]
        others=[hit for hit in others if not conflicts_with_person(hit)]
        if not track_people:
            return others
        if any(h['confidence']>=c['high_threshold'] for h in people):self.last_candidate=now
        detection=Detections(xyxy=np.array([h['box'] for h in people],dtype=np.float32).reshape(-1,4),
            confidence=np.array([h['confidence'] for h in people],dtype=np.float32),class_id=np.zeros(len(people),dtype=int))
        tracked=self.tracker.update_with_detections(detection)
        self.frame_times[self.tracker.frame_id]=now
        self.frame_times={i:t for i,t in self.frame_times.items() if now-t<=30}
        output=[]
        tracks={t.external_track_id:t for t in self.tracker.tracked_tracks}
        for box,score,identity in zip(tracked.xyxy,tracked.confidence,tracked.tracker_id):
            identity=int(identity);self.last_seen[identity]=now;self.boxes[identity]=box.tolist();t=tracks[identity]
            first=self.frame_times.get(t.start_frame,now)
            output.append({'class':0,'label':'person','type':'1','confidence':float(score),'box':box.tolist(),
                'track_id':identity,'track_confirmed':True,'first_seen':dt.datetime.fromtimestamp(first,dt.timezone.utc).astimezone().isoformat(' ')})
        # Sparse sampling or fast movement may prevent initial IoU association.
        # Never discard a high-confidence detector result just because no ID exists;
        # the event writer still requires two observations for these pending hits.
        for hit in people:
            if hit['confidence']>=c['high_threshold'] and not any(overlap(hit['box'],h['box'])>.5 for h in output):
                output.append({**hit,'track_confirmed':False})
        person_boxes.extend(hit['box'] for hit in output if hit['track_confirmed'])
        # Bounded metadata; do not let a long-running camera retain every old ID.
        self.last_seen={i:t for i,t in self.last_seen.items() if now-t<=30};self.boxes={i:b for i,b in self.boxes.items() if i in self.last_seen}
        self.stats={'tracker':'ByteTrack','confirmed_people':sum(h['track_confirmed'] for h in output),'pending_people':sum(not h['track_confirmed'] for h in output),'retained_ids':len(recent),'roi_checks':roi_count,'high_threshold':c['high_threshold'],'low_threshold':c['low_threshold']}
        return [hit for hit in others if not conflicts_with_person(hit)]+output
