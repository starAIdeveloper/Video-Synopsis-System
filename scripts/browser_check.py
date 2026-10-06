"""Reproducible end-to-end test with screenshots and optional local server startup."""
import atexit
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

BASE=os.environ.get('SYNOPSIS_URL','http://127.0.0.1:8000')
ARTIFACTS=Path('artifacts');ARTIFACTS.mkdir(exist_ok=True)
if os.environ.get('SYNOPSIS_START_SERVER')=='1':
    log=(ARTIFACTS/'server.log').open('w')
    server=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port','8000'],stdout=log,stderr=log)
    atexit.register(server.terminate)
    for _ in range(100):
        try:urllib.request.urlopen(BASE+'/api/health',timeout=1).close();break
        except OSError:time.sleep(.1)
    else:raise RuntimeError('Local API did not start')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.environ.get('SYNOPSIS_CHROMIUM'),args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1536,'height':1050})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(BASE)
    expect(page.locator('h1')).to_contain_text('Video Synopsis')
    page.locator('#sampleBtn').click()
    expect(page.locator('#sourceBadge')).to_have_text('GENERATED SAMPLE',timeout=120000)
    expect(page.locator('#sampleBtn')).to_be_enabled(timeout=120000)
    assert int(page.locator('#eventCount').inner_text())>0
    identity=page.evaluate("localStorage.getItem('synopsis-recording')")
    result=page.request.get(BASE+f'/api/jobs/{identity}/result').json()
    assert result['synopsis_duration']<result['duration']
    page.locator('[data-mode=synopsis]').click()
    expect(page.locator('#modeBanner')).to_be_visible()
    page.wait_for_function("document.querySelector('#video').readyState>=2")
    assert page.locator('#video').evaluate('(v)=>v.duration')<result['duration']
    page.locator('#video').evaluate('(v)=>{v.currentTime=2;}')
    page.screenshot(path=str(ARTIFACTS/'synopsis-desktop.png'),full_page=True)
    page.locator('[data-mode=reel]').click()
    expect(page.locator('#modeBanner')).to_be_hidden()
    page.wait_for_function("document.querySelector('#video').readyState>=2")
    page.locator('.moment').first.click()
    expect(page.locator('#detailContent')).to_be_visible()
    page.locator('#eventNote').fill('Reviewed <script>unsafe</script>')
    page.locator('#eventTags').fill('entrance, review')
    page.get_by_role('button',name='Save review',exact=True).click()
    expect(page.locator('#notice')).to_contain_text('saved')
    page.locator('#search').fill('entrance')
    assert page.locator('.event').count()==1
    assert page.locator('.event script').count()==0
    page.locator('#category').select_option('vehicle')
    expect(page.locator('#filterCount')).to_have_text('0 events')
    page.locator('#resetFilters').click()
    with page.expect_download() as download:page.locator('#synopsisDownload').click()
    download.value.save_as(ARTIFACTS/'downloaded-synopsis.mp4')
    assert (ARTIFACTS/'downloaded-synopsis.mp4').stat().st_size>1000
    with page.expect_download() as report:page.locator('#csvDownload').click()
    report.value.save_as(ARTIFACTS/'events.csv')
    assert 'motion candidate' in (ARTIFACTS/'events.csv').read_text()
    page.reload()
    expect(page.locator('#sourceBadge')).to_have_text('GENERATED SAMPLE',timeout=30000)
    expect(page.locator('#eventNote')).to_have_value('Reviewed <script>unsafe</script>')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(ARTIFACTS/'synopsis-mobile.png'),full_page=True)
    page.locator('#uploadBtn').click()
    page.locator('#uploadFile').set_input_files(str(Path('data')/identity/'source.mp4'))
    page.locator('#cameraName').fill('Uploaded entrance recording')
    page.get_by_role('button',name='Start analysis',exact=True).click()
    expect(page.locator('#sourceBadge')).to_have_text('UPLOADED RECORDING',timeout=120000)
    expect(page.locator('#sampleBtn')).to_be_enabled(timeout=120000)
    expect(page.locator('#cameraTitle')).to_have_text('Uploaded entrance recording')
    page.locator('#recordingSelect').select_option(identity)
    expect(page.locator('#sourceBadge')).to_have_text('GENERATED SAMPLE',timeout=30000)
    assert not errors,errors
    (ARTIFACTS/'browser-report.json').write_text(json.dumps({'status':'passed','viewports':['1536x1050','390x844'],
        'checks':['generated sample','actual synopsis duration','three playback modes','event source seeking','safe review notes',
        'tag search','category filter','MP4 download','CSV export','persistence after reload','mobile overflow','real upload','recording switching'],
        'console_errors':errors},indent=2))
    browser.close()
    print('Browser checks passed: playback modes, downloads, filters, annotations, upload, desktop and mobile')
