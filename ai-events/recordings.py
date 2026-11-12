"""Read-only recording index. Browser identifiers never become arbitrary paths."""
import datetime as dt,pathlib,re
from camera_registry import ordinary

class RecordingMixin:
    def recording_root(self,channel_id):
        camera=next((c for c in self.cameras() if c['channel_id']==channel_id),None)
        if not camera:raise ValueError('未知录像通道')
        root=self.records/camera['device_id']/camera['number']
        for folder in (self.records,self.records/camera['device_id'],root):
            if folder.exists() and not ordinary(folder):raise ValueError('录像目录含有链接')
        if not root.resolve().is_relative_to(self.records):raise ValueError('录像目录超出范围')
        return camera,root

    def recording_file(self,channel_id,name):
        if not re.fullmatch(r'\d{14}-[1-9]\d*\.mp4',name):raise ValueError('录像文件标识无效')
        camera,root=self.recording_root(channel_id)
        dt.datetime.strptime(name[:14],'%Y%m%d%H%M%S')
        folder=root/name[:8];file=folder/name
        if not ordinary(folder) or not ordinary(file) or not file.is_file() or not file.resolve().is_relative_to(root.resolve()):raise ValueError('录像不存在或已清理')
        if not file.stat().st_size:raise ValueError('录像尚未生成')
        return file

    def recordings(self,channel_id,date=None):
        camera,root=self.recording_root(channel_id);days=[]
        if root.exists():
            for folder in root.iterdir():
                if folder.is_dir() and ordinary(folder) and re.fullmatch(r'\d{8}',folder.name):
                    if any(re.fullmatch(r'\d{14}-[1-9]\d*\.mp4',f.name) and ordinary(f) and f.is_file() for f in folder.iterdir()):
                        try:days.append(dt.datetime.strptime(folder.name,'%Y%m%d').date().isoformat())
                        except ValueError:continue
        days.sort(reverse=True)
        date=date or (days[0] if days else dt.date.today().isoformat())
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ValueError('录像日期格式无效')
        day=dt.date.fromisoformat(date);folder=root/day.strftime('%Y%m%d');items=[]
        if folder.exists() and ordinary(folder):
            for file in folder.iterdir():
                match=re.fullmatch(r'(\d{14})-([1-9]\d*)\.mp4',file.name)
                if not match or not ordinary(file) or not file.is_file():continue
                try:size=file.stat().st_size
                except FileNotFoundError:continue
                if not size:continue
                try:start=dt.datetime.strptime(match[1],'%Y%m%d%H%M%S')
                except ValueError:continue
                if start.date()!=day:continue
                seconds=int(match[2])/1000
                url=f'/api/recordings/{channel_id}/{file.name}'
                items.append({'name':file.name,'start_time':start.isoformat(' '),'start':start.hour*3600+start.minute*60+start.second,'duration':seconds,'bytes':size,'url':url+'/video','download':url+'/download'})
        items.sort(key=lambda r:r['start'])
        return {'channel_id':channel_id,'name':camera['name'],'date':date,'days':days,'items':items}
