import cv2
import numpy as np
import pytest
from engine import Tracker,overlaps,schedule,activity_windows,analyze
from sample import generate

def detection(x,label='motion candidate'):
    return {'box':[x,.3,.05,.1],'label':label,'category':'motion','confidence':None}

def test_tracker_reorders_and_expires_ids():
    tracker=Tracker();first=tracker.update([detection(.2),detection(.7)],0)
    second=tracker.update([detection(.71),detection(.21)],.2)
    assert [d['id'] for d in second]==[first[1]['id'],first[0]['id']]
    assert tracker.update([detection(.21)],2)[0]['id'] not in [d['id'] for d in first]

def test_tracker_does_not_switch_semantic_class():
    tracker=Tracker();a=tracker.update([detection(.2,'person')],0)[0]['id']
    assert tracker.update([detection(.2,'car')],.2)[0]['id']!=a

def tube(identity,start,rect,length=4):
    return {'id':identity,'start':start,'samples':[{'offset':i,'rect':rect} for i in range(length)]}

def test_scheduler_overlaps_time_for_spatially_separate_objects():
    result=schedule([tube(1,0,[0,0,10,10]),tube(2,40,[30,0,10,10])])
    assert result[0]['start']==result[1]['start']==0
    assert max(p['end'] for p in result)==4

def test_scheduler_avoids_collisions_and_preserves_internal_gaps():
    a=tube(1,0,[0,0,10,10]);b=tube(2,20,[2,2,10,10]);b['samples'][1]['offset']=5;b['samples']=b['samples'][:2]
    result=schedule([a,b]);assert result[1]['start']==4 and result[1]['end']==10
    for left in a['samples']:
        for right in b['samples']:assert result[0]['start']+left['offset']!=result[1]['start']+right['offset']

def test_boxes_touching_edges_do_not_collide():
    assert not overlaps([0,0,10,10],[10,0,10,10])
    assert overlaps([0,0,10,10],[9,0,10,10])

def test_windows_merge_padding_clip_to_source_and_keep_chronology():
    events=[{'start':3,'end':4},{'start':1,'end':2.4},{'start':9,'end':10}]
    assert activity_windows(events,10)==[[.5,4.5],[8.5,10]]

def video_duration(path):
    cap=cv2.VideoCapture(str(path))
    try:return cap.get(cv2.CAP_PROP_FRAME_COUNT)/cap.get(cv2.CAP_PROP_FPS)
    finally:cap.release()

def test_generated_clip_has_real_tracks_and_shorter_synopsis(tmp_path):
    generate(tmp_path/'source.mp4');result=analyze(tmp_path/'source.mp4',tmp_path)
    assert result['duration']==24
    assert result['events'] and all(e['category']=='motion' and e['confidence'] is None for e in result['events'])
    assert 0<result['synopsis_duration']<result['duration']
    assert video_duration(tmp_path/'synopsis.mp4')==pytest.approx(result['synopsis_duration'],abs=.05)
    assert video_duration(tmp_path/'reel.mp4')==pytest.approx(result['reel_duration'],abs=.05)
    assert video_duration(tmp_path/'original.mp4')==pytest.approx(24,abs=.05)
    assert all((tmp_path/e['thumbnail']).exists() for e in result['events'])

def test_empty_scene_outputs_playable_background_without_invented_events(tmp_path):
    path=tmp_path/'blank.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),24,(320,180))
    for _ in range(48):out.write(np.full((180,320,3),80,dtype=np.uint8))
    out.release();result=analyze(path,tmp_path)
    assert result['events']==[] and result['placements']==[]
    assert video_duration(tmp_path/'reel.mp4')==pytest.approx(1/result['sample_rate'],abs=.02)

def test_corrupt_clip_has_readable_error(tmp_path):
    p=tmp_path/'source.mp4';p.write_bytes(b'not video')
    with pytest.raises(ValueError,match='decode'):analyze(p,tmp_path)
