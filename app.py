from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
ROOT=Path(__file__).resolve().parent
app=FastAPI(title='Video Synopsis System')
@app.get('/api/health')
def health():return {'status':'ok'}
app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='dashboard')
