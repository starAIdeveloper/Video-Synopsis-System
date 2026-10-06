"""Original generated security-camera fixture, not real surveillance footage."""
import cv2
import numpy as np

def generate(path,seconds=24):
    w,h,fps=960,540,20
    out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),fps,(w,h))
    if not out.isOpened():raise RuntimeError('Sample encoder unavailable')
    try:
        for n in range(seconds*fps):
            f=np.full((h,w,3),(53,43,29),dtype=np.uint8)
            for y in range(170,540,60):cv2.line(f,(0,y),(w,y),(66,55,40),1)
            for x in range(0,960,100):cv2.line(f,(x,170),(x,540),(66,55,40),1)
            cv2.rectangle(f,(20,25),(940,165),(75,68,50),-1)
            for x in [280,480,680]:
                cv2.rectangle(f,(x,30),(x+170,155),(125,115,81),-1)
                cv2.line(f,(x+85,30),(x+85,155),(170,165,145),3)
            for x in [40,805]:
                cv2.rectangle(f,(x,220),(x+115,470),(71,68,54),-1)
                for y in range(245,470,48):cv2.line(f,(x,y),(x+115,y),(160,140,90),3)
            t=n/fps
            for i,(a,b) in enumerate([(1,7),(9,15),(17,23)]):
                if not a<=t<b:continue
                x=int(245+(t-a)*62);y=260+i*70
                color=[(230,157,48),(60,210,139),(78,98,225)][i]
                cv2.circle(f,(x,y-34),11,(170,192,220),-1)
                cv2.rectangle(f,(x-13,y-19),(x+13,y+25),color,-1)
                cv2.line(f,(x-7,y+23),(x-10,y+45),(197,198,208),6)
                cv2.line(f,(x+7,y+23),(x+10,y+45),(197,198,208),6)
            cv2.putText(f,'GENERATED TEST FOOTAGE',(24,525),cv2.FONT_HERSHEY_SIMPLEX,.5,(200,220,230),1)
            out.write(f)
    finally:out.release()
