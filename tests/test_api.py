import time
import pytest
from fastapi.testclient import TestClient
import app as service
from sample import generate

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'DATA',tmp_path)
    with TestClient(service.app) as c:yield c

def wait(client,identity):
    for _ in range(300):
        state=client.get('/api/jobs/'+identity).json()
        if state['status'] in {'error','ready'}:return state
        time.sleep(.1)
    pytest.fail('Job did not finish')

def test_upload_validation_and_media_path_protection(client):
    assert client.post('/api/upload',files={'file':('clip.txt',b'a')}).status_code==415
    assert client.post('/api/upload',files={'file':('empty.mp4',b'')}).status_code==400
    assert client.post('/api/upload',files={'file':('clip.mp4',b'a')},data={'camera':' '}).status_code==422
    assert client.post('/api/upload',files={'file':('clip.mp4',b'a')},data={'started_at':'bad'}).status_code==422
    assert client.get('/api/jobs/not-an-id/result').status_code==404
    assert client.get('/api/health').json()=={'status':'ok'}

def test_upload_synopsis_reports_notes_and_persistence(client,tmp_path):
    source=tmp_path/'fixture.mp4';generate(source,seconds=8)
    r=client.post('/api/upload',files={'file':('source.mp4',source.read_bytes(),'video/mp4')},data={'camera':'Entrance','started_at':'2026-10-01T09:00:00Z'})
    assert r.status_code==202
    identity=r.json()['id'];assert wait(client,identity)['status']=='ready'
    result=client.get(f'/api/jobs/{identity}/result').json()
    assert result['events'] and not result['synthetic'] and result['camera']=='Entrance'
    assert client.get(f'/api/jobs/{identity}/media/source.mp4').status_code==404
    clip=client.get(f'/api/jobs/{identity}/media/synopsis.mp4',headers={'Range':'bytes=0-1023'})
    assert clip.status_code==206 and len(clip.content)==1024
    event=result['events'][0]['id'];endpoint=f'/api/jobs/{identity}/events/{event}'
    assert client.post(endpoint,json={'note':'=2+2','tags':['reviewed']}).status_code==200
    assert client.post(endpoint,json={'tags':['x'*33]}).status_code==422
    assert client.get(f'/api/jobs/{identity}/export').json()['events'][0]['tags']==['reviewed']
    assert "'=2+2" in client.get(f'/api/jobs/{identity}/export?format=csv').text
    service.jobs.pop(identity)
    assert client.get('/api/jobs/'+identity).json()['status']=='ready'
    assert client.get('/api/recordings').json()[0]['id']==identity

def test_corrupt_job_error_survives_restart(client):
    r=client.post('/api/upload',files={'file':('bad.mp4',b'bad')})
    identity=r.json()['id'];assert wait(client,identity)['status']=='error'
    service.jobs.pop(identity)
    assert client.get('/api/jobs/'+identity).json()['status']=='error'
    assert client.get('/api/recordings').json()==[]
