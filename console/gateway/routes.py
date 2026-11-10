"""One route table; recorded provenance is never a routing preference."""
from .. import config, store

DEFAULT_ROUTES = {
    'analysis':'openai:gpt-5.6-sol', 'script':'openai:gpt-5.6-sol',
    'check':'follow:script', 'dialogue_proposal':'follow:script', 'director':'follow:script', 'segment_plan':'follow:script',
    'speech_lines':'follow:analysis', 'storyboard_plan':'follow:analysis', 'storyboard_image':'official:gpt-image-2',
    'cast':'follow:analysis', 'camera_prompt':'follow:analysis', 'camera_image':'official:gpt-image-2',
    'video':'third_party:seedance2.0-mini-video',
}
STEP_LABELS = {'speech_lines':'台词断句', 'segment_plan':'分段方案', 'director':'导演方案', 'storyboard_plan':'改图方案', 'storyboard_image':'新分镜生图', 'cast':'选角识别与剧情检查', 'analysis':'拆解', 'script':'生成新脚本', 'check':'动作对比与安全检查',
               'dialogue_proposal':'台词改写方案', 'camera_prompt':'主镜头图提示词',
               'camera_image':'主镜头图生图', 'video':'分段视频生成'}


def project_data(project):
    return store.load(project) if isinstance(project, str) else project or {}


def parse(value, step, values=None):
    if isinstance(value, dict):
        channel, model = value.get('channel') or value.get('provider'), value.get('model')
    else:
        channel, sep, model = str(value).partition(':')
        model = model if sep else None
    if step in {'camera_image','storyboard_image','image'}:
        channel = {'openai':'official','jimmy':'third_party'}.get(channel,channel)
        valid = {'official','third_party'}
    elif step == 'video':
        channel = {'jimmy':'third_party'}.get(channel,channel)
        valid = {'third_party'}
    else:
        channel = {'official':'openai','third_party':'jimmy'}.get(channel,channel)
        valid = set(config.PROVIDERS)
    if channel not in valid:
        raise ValueError('未知或不支持该用途的模型渠道')
    if not model:
        values = config.load() if values is None else values
        if step in {'camera_image','storyboard_image','image'}:
            model = values.get('OPENAI_IMAGE_MODEL') if channel == 'official' else 'gpt-image-2'
        elif step == 'video': model = 'seedance2.0-mini-video'
        else: model = values.get('JIMMY_LLM_MODEL' if channel == 'jimmy' else channel.upper() + '_MODEL')
    return {'channel':channel, 'model':model or ''}


def resolve(step, project=None, override=None, *, values=None, _seen=()):
    if step not in DEFAULT_ROUTES:
        raise ValueError('未知模型步骤：' + step)
    if step in _seen:
        raise ValueError('渠道路由不能循环跟随')
    values = config.load() if values is None else values
    data = project_data(project)
    selection = override or (data.get('routes') or {}).get(step) or values.get('ROUTE_' + step.upper()) or DEFAULT_ROUTES[step]
    if isinstance(selection, str) and selection.startswith('follow:'):
        return resolve(selection.split(':',1)[1],data,values=values,_seen=(*_seen,step))
    route = parse(selection,step,values)
    # Missing-key fallback happens before any request; attempted failures never switch.
    if route['channel'] in {'openai','official'} and not values.get('OPENAI_API_KEY'):
        fallback = 'third_party' if step in {'camera_image','storyboard_image'} else values.get('DEFAULT_LLM') or 'jimmy'
        if fallback == 'openai': fallback = 'jimmy'
        route = parse(fallback,step,values)
    return route


def public_routes(project=None):
    values = config.load()
    return {step:resolve(step,project,values=values) for step in DEFAULT_ROUTES}


def image_quality(step, project=None, override=None, *, values=None):
    """Resolve quality once at preview time, separately from channel fallback."""
    if step not in {'storyboard_image', 'camera_image'}:
        raise ValueError('未知生图步骤：' + step)
    values = config.load() if values is None else values
    selection = override or (project_data(project).get('routes') or {}).get(step)
    quality = (selection.get('quality') if isinstance(selection, dict) else None)
    if quality is None:
        quality = values.get(step.upper() + '_QUALITY', 'auto')
    if quality not in {'auto', 'high'}:
        raise ValueError('生图画质只能为 auto 或 high：' + step)
    return quality


def remember(pid, step, selection):
    route = parse(selection,step)
    store.update(pid,lambda data:data.setdefault('routes',{}).__setitem__(step,route))
    return route


def validate(values):
    """Reject malformed or cross-capability follows before publishing settings."""
    follows = {'speech_lines':'analysis', 'segment_plan':'script', 'director':'script', 'storyboard_plan':'analysis', 'cast':'analysis', 'check':'script','dialogue_proposal':'script','camera_prompt':'analysis'}
    for step in ('storyboard_image', 'camera_image'):
        image_quality(step, values=values)
    for step in DEFAULT_ROUTES:
        selection = values.get('ROUTE_' + step.upper()) or DEFAULT_ROUTES[step]
        if str(selection).startswith('follow:') and selection != 'follow:' + follows.get(step,''):
            raise ValueError('该步骤不支持此跟随路由：' + step)
        resolve(step,values=values)
