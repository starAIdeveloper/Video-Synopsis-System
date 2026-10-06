"""Local single-user video synopsis API."""
import csv
import io
import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from typing import Literal
from engine import analyze
from sample import generate

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('SYNOPSIS_DATA',str(ROOT/'data')))
DATA.mkdir(exist_ok=True,parents=True)
app=FastAPI(title='Video Synopsis System',version='1.0.0')
executor=ThreadPoolExecutor(max_workers=1)
slots=threading.BoundedSemaphore(3)
lock=threading.RLock()
jobs={}

class Annotation(BaseModel):
    note:str=Field(default='',max_length=250)
    tags:list[str]=Field(default_factory=list,max_length=8)

def folder(identity):
    if not re.fullmatch('[a-f0-9]{32}',identity):raise HTTPException(404,'Recording not found')
    p=DATA/identity
    if not p.is_dir():raise HTTPException(404,'Recording not found')
    return p

def read_json(path):
    with lock:return json.loads(path.read_text())

def write_json(path,value):
    with lock:
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(value,allow_nan=False))
        temp.replace(path)

def get_result(identity):
    p=folder(identity)/'result.json'
    if not p.exists():raise HTTPException(409,'Analysis is not ready')
    return read_json(p)

def run(identity,meta):
    p=folder(identity)
    try:
        with lock:jobs[identity]['status']='processing'
        if meta['synthetic']:generate(p/'source.mp4')
        if not shutil.which('ffmpeg'):raise ValueError('Install FFmpeg before analyzing video.')
        def progress(v):
            with lock:jobs[identity]['progress']=round(v*100)
        result=analyze(p/'source.mp4',p,progress,weights=os.environ.get('SYNOPSIS_MODEL'))
        result.update(meta)
        write_json(p/'result.json',result)
        with lock:jobs[identity].update(status='ready',progress=100)
    except Exception as e:
        with lock:jobs[identity].update(status='error',error=str(e)[:350])
        write_json(p/'error.json',{'error':str(e)[:350]})
    finally:slots.release()

def queue(identity,meta):
    write_json(folder(identity)/'meta.json',meta)
    with lock:jobs[identity]={'id':identity,'status':'queued','progress':0}
    executor.submit(run,identity,meta)
    return {'id':identity,'status':'queued','progress':0}

@app.get('/api/health')
def health():return {'status':'ok'}

@app.post('/api/upload',status_code=202)
async def upload(file:UploadFile=File(...),camera:str=Form('Camera 01 - Entrance'),started_at:str=Form('')):
    camera=camera.strip()
    if not 1<=len(camera)<=64:raise HTTPException(422,'Camera name must contain 1 to 64 characters')
    if started_at:
        try:datetime.fromisoformat(started_at)
        except ValueError:raise HTTPException(422,'Recording start must be an ISO date/time')
    if Path(file.filename or '').suffix.lower() not in {'.mp4','.mov','.avi','.mkv','.webm'}:
        raise HTTPException(415,'Use MP4, MOV, AVI, MKV or WebM')
    if not slots.acquire(blocking=False):raise HTTPException(429,'Queue full. Try again soon.')
    identity=uuid.uuid4().hex;p=DATA/identity;p.mkdir()
    try:
        size=0
        with (p/'source.mp4').open('wb') as out:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>200*1024*1024:raise HTTPException(413,'Maximum upload size is 200 MB')
                out.write(chunk)
        if not size:raise HTTPException(400,'Video is empty')
        return queue(identity,{'id':identity,'camera':camera,'started_at':started_at or None,'synthetic':False})
    except Exception:
        shutil.rmtree(p,ignore_errors=True);slots.release();raise
    finally:await file.close()

@app.post('/api/sample',status_code=202)
def sample():
    if not slots.acquire(blocking=False):raise HTTPException(429,'Queue full')
    identity=uuid.uuid4().hex;(DATA/identity).mkdir()
    return queue(identity,{'id':identity,'camera':'Generated entrance scene','started_at':None,'synthetic':True})

@app.get('/api/recordings')
def recordings():
    output=[]
    for p in sorted(DATA.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
        if not p.is_dir() or not (p/'result.json').exists():continue
        r=read_json(p/'result.json')
        output.append({k:r[k] for k in ['id','camera','started_at','synthetic','duration','synopsis_duration']})
    return output

@app.get('/api/jobs/{identity}')
def status(identity:str):
    p=folder(identity)
    with lock:
        if identity in jobs:return jobs[identity].copy()
    if (p/'result.json').exists():return {'id':identity,'status':'ready','progress':100}
    if (p/'error.json').exists():return {'id':identity,'status':'error',**read_json(p/'error.json')}
    return {'id':identity,'status':'error','error':'Processing interrupted. Please upload this clip again.'}

@app.get('/api/jobs/{identity}/result')
def result(identity:str):return get_result(identity)

@app.get('/api/jobs/{identity}/media/{name}')
def media(identity:str,name:str,download:bool=False):
    p=folder(identity)
    if not re.fullmatch(r'(original|synopsis|reel)\.mp4|background\.webp|event-[0-9]+\.webp',name):
        raise HTTPException(404,'Media not found')
    file=p/name
    if not file.exists():raise HTTPException(404,'Media not ready')
    mime='video/mp4' if name.endswith('.mp4') else 'image/webp'
    return FileResponse(file,media_type=mime,filename=name if download else None)

@app.post('/api/jobs/{identity}/events/{event_id}')
def annotate(identity:str,event_id:int,annotation:Annotation):
    if any(len(t)>32 for t in annotation.tags):raise HTTPException(422,'Tags must be at most 32 characters')
    with lock:
        r=get_result(identity)
        event=next((e for e in r['events'] if e['id']==event_id),None)
        if event is None:raise HTTPException(404,'Event not found')
        event.update(note=annotation.note,tags=annotation.tags)
        write_json(folder(identity)/'result.json',r)
        return event

@app.get('/api/jobs/{identity}/export')
def export(identity:str,format:Literal['json','csv']='json'):
    r=get_result(identity)
    if format=='json':return Response(json.dumps(r),media_type='application/json',headers={'Content-Disposition':'attachment; filename=analysis.json'})
    stream=io.StringIO();writer=csv.writer(stream)
    writer.writerow(['track_id','start_s','end_s','label','category','confidence','long_activity','note','tags'])
    for e in r['events']:
        # Spreadsheet formula injection protection for user-provided fields.
        def safe(v):return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v
        writer.writerow([e['id'],e['start'],e['end'],e['label'],e['category'],e['confidence'],e['long_activity'],safe(e['note']),safe(';'.join(e['tags']))])
    return Response(stream.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=events.csv'})

app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='dashboard')
