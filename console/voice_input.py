"""Microphone uploads transcribed locally; all temporary media is always removed."""
import tempfile
from pathlib import Path
from . import asr, media

TEMP = Path(__file__).resolve().parents[1]/'runtime/temp'
MAX_BYTES = 25*1024*1024


def transcribe_stream(stream, length):
    if length<=0:raise ValueError('没有录到声音，请再试一次')
    if length>MAX_BYTES:raise ValueError('录音太长了，请分成几段说')
    TEMP.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='voice-',dir=TEMP) as folder:
            source=Path(folder)/'recording';wav=Path(folder)/'recording.wav'
            remaining=length
            with source.open('wb') as fh:
                while remaining:
                    chunk=stream.read(min(remaining,1<<20))
                    if not chunk:raise ValueError('录音上传中断，请再试一次')
                    fh.write(chunk);remaining-=len(chunk)
            media._run(['ffmpeg','-v','error','-y','-i',str(source),'-vn','-t','300','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)],120)
            value=asr.transcribe(wav,model_size='medium',language='zh',local_only=True)
            text=''.join(s.get('text','') for s in value.get('segments',[])).strip()
            if not text:raise ValueError('没有听清楚，请靠近麦克风再说一次')
            return {'text':text}
    except Exception as exc:
        raise ValueError('录音没能转成文字，请检查本地语音模型后重试：'+str(exc)) from exc
