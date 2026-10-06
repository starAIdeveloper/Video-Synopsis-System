"""Fixed-camera activity extraction, tube scheduling and real video composition."""
import math
import os
import subprocess
from collections import Counter
from pathlib import Path
import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

RATE=5
MAX_SECONDS=300

class Tracker:
    def __init__(self,gate=.13,ttl=.8):
        self.gate,self.ttl=gate,ttl
        self.active={}
        self.next_id=1
    def update(self,detections,t):
        self.active={i:v for i,v in self.active.items() if t-v['time']<=self.ttl}
        ids=list(self.active)
        centres=np.array([[d['box'][0]+d['box'][2]/2,d['box'][1]+d['box'][3]/2] for d in detections])
        matches={}
        if ids and len(centres):
            predictions=np.array([self.active[i]['point']+self.active[i]['velocity']*(t-self.active[i]['time']) for i in ids])
            cost=np.linalg.norm(predictions[:,None]-centres[None,:],axis=2)
            for a,i in enumerate(ids):
                for b,d in enumerate(detections):
                    if d['label']!=self.active[i]['label']:cost[a,b]=10
            rows,cols=linear_sum_assignment(cost)
            matches={int(c):ids[int(r)] for r,c in zip(rows,cols) if cost[r,c]<=self.gate}
        output=[]
        for n,d in enumerate(detections):
            identity=matches.get(n)
            if identity is None:identity,self.next_id=self.next_id,self.next_id+1
            old=self.active.get(identity)
            dt=t-old['time'] if old else 0
            velocity=(centres[n]-old['point'])/dt if dt>0 else np.zeros(2)
            self.active[identity]={'point':centres[n],'velocity':velocity,'time':t,'label':d['label']}
            output.append(dict(d,id=identity))
        return output

class Detector:
    def __init__(self,weights=None):
        self.background=cv2.createBackgroundSubtractorMOG2(history=180,varThreshold=35,detectShadows=False)
        self.model=None
        if weights:
            if not Path(weights).is_file():raise ValueError('SYNOPSIS_MODEL must point to an existing local detector model.')
            from ultralytics import YOLO
            self.model=YOLO(weights)
    def __call__(self,frame):
        h,w=frame.shape[:2]
        mask=self.background.apply(frame,learningRate=.003)
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
        out=[]
        if self.model:
            r=self.model.predict(frame,conf=.4,verbose=False)[0]
            names=r.names
            for b in r.boxes:
                label=names[int(b.cls[0])]
                if label not in {'person','car','truck','bus','motorcycle','bicycle','backpack','handbag'}:continue
                x,y,x2,y2=b.xyxy[0].cpu().tolist()
                category='person' if label=='person' else 'bag' if label in {'backpack','handbag'} else 'vehicle'
                out.append({'box':[x/w,y/h,(x2-x)/w,(y2-y)/h],'label':label,'category':category,'confidence':float(b.conf[0])})
        else:
            contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                x,y,bw,bh=cv2.boundingRect(c)
                if 90<cv2.contourArea(c)<w*h*.12 and bw>=8 and bh>=12:
                    out.append({'box':[x/w,y/h,bw/w,bh/h],'label':'motion candidate','category':'motion','confidence':None})
        return sorted(out,key=lambda d:d['box'][2]*d['box'][3],reverse=True)[:30],mask

def overlaps(a,b):
    x,y,w,h=a;xx,yy,ww,hh=b
    return min(x+w,xx+ww)>max(x,xx) and min(y+h,yy+hh)>max(y,yy)

def schedule(tubes):
    """Pack tubes at earliest frame without box collisions; preserve internal timing."""
    occupancy={}
    placements=[]
    for tube in sorted(tubes,key=lambda t:t['start']):
        start=0
        duration=tube['samples'][-1]['offset']+1
        while True:
            conflict=False
            for s in tube['samples']:
                if any(overlaps(s['rect'],b) for b in occupancy.get(start+s['offset'],[])):
                    conflict=True;break
            if not conflict:break
            start+=1
        placements.append({'id':tube['id'],'start':start,'end':start+duration})
        for s in tube['samples']:occupancy.setdefault(start+s['offset'],[]).append(s['rect'])
    return placements

def activity_windows(events,duration,padding=.5):
    intervals=sorted((max(0,e['start']-padding),min(duration,e['end']+padding)) for e in events)
    merged=[]
    for a,b in intervals:
        if merged and a<=merged[-1][1]+.3:merged[-1][1]=max(merged[-1][1],b)
        else:merged.append([a,b])
    return merged

def encode(frames,path,size,fps=RATE):
    w,h=size
    p=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','bgr24','-s',f'{w}x{h}','-r',str(fps),
        '-i','pipe:0','-an','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],
        stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for frame in frames:p.stdin.write(np.ascontiguousarray(frame).tobytes())
        p.stdin.close()
        err=p.stderr.read()
        if p.wait(timeout=120)!=0:raise RuntimeError('Video encoding failed: '+err.decode()[-200:])
    except Exception:
        p.kill();p.wait();raise
    finally:p.stderr.close()

def compose(background,tubes,placements,path,progress=lambda p:None,rate=RATE):
    byid={t['id']:t for t in tubes}
    maps={t['id']:{s['offset']:s for s in t['samples']} for t in tubes}
    total=max((p['end'] for p in placements),default=1)
    h,w=background.shape[:2]
    def frames():
        for n in range(total):
            image=background.copy()
            for p in placements:
                s=maps[p['id']].get(n-p['start'])
                if s is None:continue
                x,y,bw,bh=s['rect']
                region=image[y:y+bh,x:x+bw]
                alpha=s['mask'].astype(np.float32)[...,None]/255
                region[:]=(region*(1-alpha)+s['crop']*alpha).astype(np.uint8)
                cv2.rectangle(image,(x,y),(x+bw,y+bh),(238,150,43),1)
                label=f"#{p['id']} source {s['time']:.1f}s"
                cv2.putText(image,label,(x,max(15,y-5)),cv2.FONT_HERSHEY_SIMPLEX,.4,(250,208,153),1)
            cv2.putText(image,'COMPOSITED SYNOPSIS - source times differ',(12,h-12),cv2.FONT_HERSHEY_SIMPLEX,.45,(245,240,210),1)
            yield image
            if n%25==0:progress(n/max(1,total))
    encode(frames(),path,(w,h),fps=rate)
    return total/rate

def analyze(source,folder,progress=lambda p:None,weights=None,dwell_seconds=15):
    cap=cv2.VideoCapture(str(source))
    detector=Detector(weights)
    try:
        fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not cap.isOpened() or not math.isfinite(fps) or not 0<fps<=120 or count<=0:
            raise ValueError('Cannot decode video. Try a standard H.264 MP4.')
        if count/fps>MAX_SECONDS or count>36000:raise ValueError('Use clips up to five minutes. Split longer recordings first.')
        stride=max(1,math.ceil(fps/RATE));tracker=Tracker();records=[];tracks={};backgrounds=[];n=0;sample_index=0;memory=0
        while True:
            ok,frame=cap.read()
            if not ok:break
            if n%stride==0:
                h0,w0=frame.shape[:2]
                if w0*h0>16000000:raise ValueError('Resolution exceeds supported limit.')
                scale=min(960/w0,540/h0,1)
                w=max(2,int(w0*scale)//2*2);h=max(2,int(h0*scale)//2*2)
                frame=cv2.resize(frame,(w,h))
                if sample_index%10==0 and len(backgrounds)<30:backgrounds.append(frame.copy())
                detections,mask=detector(frame)
                # Ignore initial background-model warmup, which marks the whole scene.
                if sample_index<3:detections=[]
                detections=tracker.update(detections,n/fps)
                for d in detections:
                    x,y,bw,bh=d['box'];x=max(0,int(x*w)-2);y=max(0,int(y*h)-2)
                    x2=min(w,int((d['box'][0]+d['box'][2])*w)+2);y2=min(h,int((d['box'][1]+d['box'][3])*h)+2)
                    if x2<=x or y2<=y:continue
                    t=tracks.setdefault(d['id'],{'id':d['id'],'start':sample_index,'samples':[],'labels':[],'category':d['category'],'confidence':[]})
                    crop=frame[y:y2,x:x2].copy();alpha=mask[y:y2,x:x2].copy()
                    if detector.model and np.count_nonzero(alpha)<alpha.size*.05:alpha[:]=255
                    memory+=crop.nbytes+alpha.nbytes
                    if memory>200*1024*1024:raise ValueError('Too much activity for this clip. Use a shorter segment.')
                    t['samples'].append({'offset':sample_index-t['start'],'time':n/fps,'rect':[x,y,x2-x,y2-y],'crop':crop,'mask':alpha})
                    t['labels'].append(d['label'])
                    if d['confidence'] is not None:t['confidence'].append(d['confidence'])
                records.append({'time':round(n/fps,4),'detections':detections})
                sample_index+=1
                progress(min(.60,n/max(1,count)*.60))
            n+=1
        if not records:raise ValueError('No frames decoded.')
        duration=n/fps
        candidates=[t for t in tracks.values() if len(t['samples'])>=3]
        candidates=sorted(candidates,key=lambda t:len(t['samples']),reverse=True)[:40]
        events=[]
        for t in candidates:
            first,last=t['samples'][0],t['samples'][-1]
            start,end=first['time'],min(duration,last['time']+stride/fps)
            label=Counter(t['labels']).most_common(1)[0][0]
            thumb=f"event-{t['id']}.webp"
            middle=t['samples'][len(t['samples'])//2]
            cv2.imwrite(str(folder/thumb),middle['crop'],[cv2.IMWRITE_WEBP_QUALITY,80])
            events.append({'id':t['id'],'start':round(start,3),'end':round(end,3),'duration':round(end-start,3),
                'label':label,'category':t['category'],'long_activity':end-start>=dwell_seconds,
                'confidence':round(sum(t['confidence'])/len(t['confidence']),3) if t['confidence'] else None,
                'thumbnail':thumb,'observations':len(t['samples']),'note':'','tags':[]})
        events.sort(key=lambda e:e['start'])
        background=np.median(np.stack(backgrounds),axis=0).astype(np.uint8)
        cv2.imwrite(str(folder/'background.webp'),background,[cv2.IMWRITE_WEBP_QUALITY,80])
        placements=schedule(candidates)
        synopsis_duration=compose(background,candidates,placements,folder/'synopsis.mp4',lambda v:progress(.65+v*.15),rate=fps/stride)
        windows=activity_windows(events,duration)
        reel_map=[];cursor=0;reel_count=0
        # Stream the second decoding pass instead of retaining full frames in RAM.
        cap.set(cv2.CAP_PROP_POS_FRAMES,0)
        def reel_frames():
            nonlocal reel_count
            frame_index=0
            while True:
                ok,frame=cap.read()
                if not ok:break
                time=frame_index/fps
                if frame_index%stride==0 and any(a<=time<b for a,b in windows):
                    reel_count+=1
                    yield cv2.resize(frame,(w,h))
                frame_index+=1
            if not reel_count:
                reel_count=1
                yield background
        for a,b in windows:
            included=[r['time'] for r in records if a<=r['time']<b]
            length=len(included)/(fps/stride)
            reel_map.append({'output_start':cursor,'output_end':cursor+length,'source_start':included[0] if included else a,'source_end':b})
            cursor+=length
        encode(reel_frames(),folder/'reel.mp4',(w,h),fps=fps/stride)
        subprocess.run(['ffmpeg','-y','-v','error','-i',str(source),'-an','-vf',f'scale={w}:{h}',
            '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-movflags','+faststart',str(folder/'original.mp4')],
            capture_output=True,check=True,timeout=240)
        progress(1)
        return {'duration':round(duration,3),'fps':fps,'sample_rate':fps/stride,'width':w,'height':h,
            'events':events,'frames':records,'placements':placements,'synopsis_duration':round(synopsis_duration,3),
            'reel_duration':reel_count/(fps/stride),'reel_map':reel_map,'detector':'model' if weights else 'motion',
            'dwell_seconds':dwell_seconds,'omitted_tracks':max(0,len(tracks)-len(candidates))}
    finally:cap.release()
