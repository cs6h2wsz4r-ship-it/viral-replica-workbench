"""Business-facing model gateway; legacy clients remain forwarding surfaces."""
from __future__ import annotations
import time
from contextvars import ContextVar
from .. import config, store
from ..channel_text import user_message
from .errors import GatewayError, LLMError, ImageError, classify_error, sanitize
from . import routes, usage
from .retry import classify

_depth = ContextVar('gateway_depth',default=0)


def label(channel, model=None):
    names = {'openai':'官方','official':'官方','jimmy':'三方渠道','third_party':'三方渠道',
             'third-party':'三方渠道','deepseek':'三方渠道 · DeepSeek','doubao':'三方渠道 · 豆包','custom':'三方渠道 · 自定义'}
    name = names.get(channel,user_message(channel))
    if model:
        display = 'GPT-5.6 Sol' if model == 'gpt-5.6-sol' else user_message(model)
        return name + ' · ' + display
    return name


def channels():
    values = config.load()
    result = {}
    for channel,meta in config.PROVIDERS.items():
        prefix = 'JIMMY_LLM' if channel == 'jimmy' else channel.upper()
        model = values.get(prefix+'_MODEL','')
        result[channel] = {**meta,'label':label(channel,model),'model':model,
            'ready':bool((config.jimmy_configured() if channel == 'jimmy' else values.get(channel.upper()+'_API_KEY')) and model and values.get(prefix+'_BASE_URL')),
            'video_native':channel=='jimmy' and model.lower().startswith('gemini')}
    return result


def _call(step,route,kind,project,fn,resource=None):
    token = _depth.set(1)
    state,usage_token = usage.begin(project,step,route['channel'],route.get('model') or '',kind,resource)
    result,error = None,None
    try:
        result = fn()
        if isinstance(result,dict) and result.get('model'):
            state['model'] = result['model']
            state['channel'] = result.get('provider') or state['channel']
        elif isinstance(result,tuple) and len(result)>1 and isinstance(result[1],dict):
            state['model'] = result[1].get('requested_model') or result[1].get('model') or state['model']
            state['channel'] = result[1].get('provider') or state['channel']
        return result
    except Exception as exc:
        error = exc
        if isinstance(exc,GatewayError):
            if exc.kind == 'unknown': exc.kind = classify_error(exc)
            exc.channel = exc.channel or route['channel']
            exc.model = exc.model or route.get('model') or ''
            raise
        from ..jimmy_bridge import BridgeError
        cls = BridgeError if kind in {'image','video'} else LLMError
        error = cls(str(exc))
        error.kind = classify_error(exc)
        error.technical = sanitize(exc)
        error.channel,error.model = route['channel'],route.get('model') or ''
        raise error from exc
    finally:
        try:
            if not state['deferred'] or error is not None: usage.finish(state,result,error)
        finally:
            usage.reset(usage_token)
            _depth.reset(token)


def _reason(step,messages,*,json=False,checked=False,max_tokens=8000,project=None,override=None,_compat=False,**kw):
    from .. import llm
    from . import reasoning
    if _compat:
        route = override if isinstance(override,dict) else {'channel':override,'model':kw.get('model')}
    else:
        route = routes.resolve(step,project,override)
    if route.get('model') and not kw.get('model'): kw['model'] = route['model']
    method = 'chat_json_checked' if checked else 'chat_json' if json else 'chat'
    client = reasoning if _compat else llm
    operation = lambda:getattr(client,method)(route['channel'],messages,max_tokens=max_tokens,**kw)
    if _compat and _depth.get():
        try: return operation()
        except Exception as exc:
            if not checked:
                usage.capture_error(route['channel'],route.get('model'),exc)
            raise
    return _call(step,route,'reason',project,operation)


def reason(step,messages,*,json=False,max_tokens=8000,project=None,override=None,**kw):
    return _reason(step,messages,json=json,max_tokens=max_tokens,project=project,override=override,**kw)


def reason_checked(step,messages,*,max_tokens=8000,project=None,override=None,**kw):
    return _reason(step,messages,checked=True,max_tokens=max_tokens,project=project,override=override,**kw)


def image_generate(preview,output,*,before_send=None,project=None,_compat=False):
    from .adapters import official
    operation = lambda:official.image_generate(preview,output,before_send=before_send)
    if _compat and _depth.get(): return operation()
    return _call('camera_image',{'channel':'official','model':preview.get('model')},'image',project,operation)


def image_preview(step,pid,*args,override=None,**kw):
    from .adapters import third_party
    values = config.load()
    project = store.load(pid)
    route = routes.resolve(step,project,override,values=values)
    kw['channel'] = route['channel']
    if route['channel'] == 'official':
        kw['quality'] = routes.image_quality(step,project,override,values=values)
    # Legacy bridge positional fields include model. Preserve their ordering.
    args = list(args)
    if len(args)>3: args[3] = route['model']
    else: kw['model'] = route['model']
    return _call(step,route,'preview',pid,lambda:third_party.image_preview(pid,*args,**kw))


def _image_action(action,pid,iid):
    from .adapters import third_party
    rec = store.load(pid).get('images',{}).get(iid,{})
    route = {'channel':rec.get('channel') or 'third_party','model':rec.get('preview',{}).get('model') or 'gpt-image-2'}
    step = 'storyboard_image' if rec.get('storyboard_context') else 'camera_image'
    return _call(step,route,'image',pid,lambda:getattr(third_party,action)(pid,iid),iid)


def image_confirm(pid,iid): return _image_action('image_confirm',pid,iid)
def image_resume(pid,iid): return _image_action('image_resume',pid,iid)


def _video(action,pid,tid,*args,override=None,fix_plan=None):
    from .adapters import third_party
    route = routes.resolve('video',pid,override) if action == 'preview' else {'channel':'third_party','model':'seedance2.0-mini-video'}
    options = {}
    if action == 'preview':
        explicit = override or (store.load(pid).get('routes') or {}).get('video')
        if explicit or route['model'] != 'seedance2.0-mini-video': options['model'] = route['model']
        if fix_plan is not None: options['fix_plan'] = fix_plan
    if action != 'preview' and args:
        run = next((r for r in store.load(pid).get('runs',{}).get(tid,[]) if r.get('rev')==args[0]),{})
        route['model'] = run.get('model') or route['model']
    return _call('video',route,'video',pid,lambda:third_party.video(action,pid,tid,*args,**options),
                 f'{tid}:{args[0]}' if args else None)


def video_preview(pid,tid,*,override=None,fix_plan=None): return _video('preview',pid,tid,override=override,fix_plan=fix_plan)
def video_confirm(pid,tid,rev,job_sha256): return _video('confirm',pid,tid,rev,job_sha256)
def video_resume(pid,tid,rev): return _video('resume',pid,tid,rev)
def video_refresh(pid,tid,rev): return _video('refresh',pid,tid,rev)
def video_resubmit(pid,tid,rev): return _video('resubmit',pid,tid,rev)
def video_recover(pid,tid,rev,remote_task_id): return _video('recover',pid,tid,rev,remote_task_id)


def video_refresh_all(pid):
    from .. import jimmy_bridge
    return jimmy_bridge.refresh_all(pid)


def test_connection(channel):
    started = time.monotonic()
    try:
        if channel not in config.PROVIDERS: raise ValueError('未知推理渠道')
        reason('script',[{'role':'user','content':'Reply with OK'}],max_tokens=16,override=channel,_compat=True)
        status,kind = '可用',None
    except (GatewayError,ValueError) as exc:
        kind = getattr(exc,'kind',None) or classify(0,str(exc))
        status = {'auth':'key 无效','quota':'额度不足'}.get(kind,'网络失败')
    return {'status':status,'error_kind':kind,'elapsed_ms':round((time.monotonic()-started)*1000)}
