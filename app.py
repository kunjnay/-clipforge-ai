from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import shutil, subprocess, uuid, json, os

BASE=Path(__file__).parent
UPLOADS=BASE/'uploads'; OUTPUTS=BASE/'clips'; DATA=BASE/'data'
for p in (UPLOADS,OUTPUTS,DATA): p.mkdir(exist_ok=True)
app=FastAPI(title='ClipForge AI')
app.mount('/clips', StaticFiles(directory=str(OUTPUTS)), name='clips')
app.mount('/static', StaticFiles(directory=str(BASE/'static')), name='static')

@app.get('/', response_class=HTMLResponse)
def home(): return (BASE/'static'/'index.html').read_text()
@app.get('/api/status')
def status():
    return {'ffmpeg':shutil.which('ffmpeg') is not None,'clips':len(list(OUTPUTS.glob('*.mp4'))),'ai_configured':bool(os.getenv('OPENAI_API_KEY'))}
@app.post('/api/upload')
async def upload_video(file: UploadFile=File(...)):
    ext=Path(file.filename or 'video.mp4').suffix.lower()
    if ext not in ['.mp4','.mov','.mkv','.webm','.avi']:
        raise HTTPException(400,'Upload a supported video file (MP4, MOV, MKV, WebM, AVI).')
    fid=uuid.uuid4().hex
    dest=UPLOADS/f'{fid}{ext}'
    with dest.open('wb') as out: shutil.copyfileobj(file.file,out)
    return {'video_id':fid,'filename':file.filename,'path':str(dest),'size':dest.stat().st_size}
@app.post('/api/clip')
async def make_clip(payload:dict):
    if not shutil.which('ffmpeg'): raise HTTPException(503,'FFmpeg is not installed. Install FFmpeg, then restart ClipForge.')
    video_id=payload.get('video_id'); start=float(payload.get('start',0)); duration=float(payload.get('duration',30))
    if not video_id or start<0 or duration<1 or duration>180: raise HTTPException(400,'Enter a valid video, start time and clip duration (1–180 seconds).')
    srcs=list(UPLOADS.glob(video_id+'.*'))
    if not srcs: raise HTTPException(404,'Uploaded source video not found.')
    outid=uuid.uuid4().hex; out=OUTPUTS/f'{outid}.mp4'; src=srcs[0]
    # Vertical output with blurred fill behind a centered crop, plus clean audio.
    vf="split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=18:2[bg];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
    cmd=['ffmpeg','-y','-ss',str(start),'-i',str(src),'-t',str(duration),'-vf',vf,'-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','128k','-movflags','+faststart',str(out)]
    try:
        proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600)
        if proc.returncode!=0: raise RuntimeError(proc.stderr.decode(errors='ignore')[-1500:])
    except subprocess.TimeoutExpired: raise HTTPException(504,'Clip rendering timed out. Try a shorter clip.')
    except Exception as e: raise HTTPException(500,'Render failed: '+str(e))
    return {'clip_id':outid,'url':f'/clips/{outid}.mp4','filename':out.name,'start':start,'duration':duration}
@app.get('/api/clips')
def list_clips():
    return [{'name':p.name,'url':f'/clips/{p.name}','size':p.stat().st_size} for p in sorted(OUTPUTS.glob('*.mp4'),key=lambda p:p.stat().st_mtime,reverse=True)]
@app.post('/api/instructions')
def save_instructions(payload:dict):
    (DATA/'instructions.json').write_text(json.dumps({'instructions':payload.get('instructions','')},indent=2))
    return {'saved':True}
@app.get('/api/instructions')
def get_instructions():
    p=DATA/'instructions.json'
    return json.loads(p.read_text()) if p.exists() else {'instructions':'Find compelling moments; prioritize reactions, punchlines, surprises and context. Avoid dead air.'}
@app.post('/api/schedule')
def schedule(payload:dict):
    p=DATA/'schedule.json'; entries=json.loads(p.read_text()) if p.exists() else []
    item={'id':uuid.uuid4().hex,'clip_url':payload.get('clip_url'),'platform':payload.get('platform','manual review'),'datetime':payload.get('datetime'),'status':'Queued for review'}
    entries.append(item); p.write_text(json.dumps(entries,indent=2)); return item
@app.get('/api/schedule')
def get_schedule():
    p=DATA/'schedule.json'; return json.loads(p.read_text()) if p.exists() else []
@app.delete('/api/schedule/{item_id}')
def delete_schedule(item_id:str):
    p=DATA/'schedule.json'; entries=json.loads(p.read_text()) if p.exists() else []
    updated=[x for x in entries if x['id']!=item_id]; p.write_text(json.dumps(updated,indent=2)); return {'deleted':len(updated)!=len(entries)}
