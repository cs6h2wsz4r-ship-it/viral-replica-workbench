"""Response-only usage ledger, also carried into background workers."""
from contextvars import ContextVar, copy_context
from copy import deepcopy
from datetime import datetime
import math
from .. import store

_current = ContextVar('gateway_usage',default=None)


def number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value >= 0 else None


def mark_network_request():
    state = _current.get()
    if state is not None:
        state['network_request'] = True


def capture_response(payload, *, channel=None, model=None):
    state = _current.get()
    if state is not None and isinstance(payload,dict):
        state['network_request'] = True
        inner = payload.get('data')
        usage = payload.get('usage') or payload.get('usageMetadata') or (inner.get('usageMetadata') if isinstance(inner,dict) else None) or {}
        state['responses'].append({**usage,'_channel':channel or state['channel'],'_model':model or state['model']})


def capture_error(channel, model, error):
    state = _current.get()
    if state is None: return
    # Only transport-level nested calls own a response; orchestration unwinds
    # through the same compatibility surface without adding duplicate events.
    events=state['responses']
    if events and events[-1].get('_channel') == channel and (not model or events[-1].get('_model') == model):
        events[-1]['_error'] = error
    elif not events or events[-1].get('_error') is not error:
        events.append({'_channel':channel,'_model':model or state['model'],'_error':error})


def begin(project,step,channel,model,kind,resource=None):
    state = {'project':project,'step':step,'channel':channel,'model':model,'kind':kind,
             'resource':resource,'responses':[],'deferred':False,'finished':False,'network_request':False}
    if project is not None and kind == 'image' and resource:
        data = store.load(project) if isinstance(project,str) else project
        rec = (data.get('images') or {}).get(resource,{})
        state['previous_image_usage'] = deepcopy(rec.get('usage'))
        state['previous_image_cost'] = _image_cost(project,rec) if channel == 'third_party' else None
    return state,_current.set(state)


def reset(token): _current.reset(token)


def _sum(values):
    given = [v for v in values if number(v) is not None]
    return sum(given) if given else None


def _image_cost(project, rec):
    if isinstance(project,str) and rec.get('run_dir'):
        import json
        path = store.project_dir(project)/rec['run_dir']/'image-state.json'
        if path.is_file():
            reported = json.loads(path.read_text()).get('cost')
            return number(reported.get('amount') if isinstance(reported,dict) else reported)
    return None


def finish(state,result=None,error=None):
    if state['finished']: return
    state['finished'] = True
    project = state['project']
    if project is None: return
    from .errors import classify_error
    responses = state['responses']
    groups = {}
    for response in responses:
        route = (response.get('_channel') or state['channel'], response.get('_model') or state['model'])
        groups.setdefault(route,[]).append(response)
    if len(groups)>1:
        for (channel,model),events in groups.items():
            child={**state,'channel':channel,'model':model,'responses':events,'finished':False}
            finish(child,error=events[-1].get('_error') or (error if events[-1] is responses[-1] else None))
        return
    if groups:
        state['channel'],state['model']=next(iter(groups))
    if not responses and isinstance(result,tuple) and len(result)>1:
        responses = [result[1].get('usage') or {}]
    if not responses and isinstance(result,dict) and result.get('usage'):
        responses = [result['usage']]
    requested = state.get('network_request', False) or bool(responses)
    tokens = {'prompt':_sum([u.get('prompt_tokens',u.get('input_tokens',u.get('promptTokenCount'))) for u in responses]),
              'completion':_sum([u.get('completion_tokens',u.get('output_tokens',u.get('candidatesTokenCount'))) for u in responses]),
              'reasoning':_sum([(u.get('completion_tokens_details') or u.get('output_tokens_details') or {}).get('reasoning_tokens',u.get('thoughtsTokenCount')) for u in responses])}
    total = _sum([u.get('total_tokens',u.get('totalTokenCount')) for u in responses])
    if total is None and tokens['prompt'] is not None and tokens['completion'] is not None:
        total = tokens['prompt'] + tokens['completion']
    billing = {'unit':'tokens','amount':total}
    if state['kind'] == 'image' and state['channel'] == 'third_party': billing = {'unit':'点','amount':None}
    data = store.load(project) if isinstance(project,str) else project
    resource = state['resource']
    if resource and state['kind'] == 'image':
        rec = (data.get('images') or {}).get(resource,{})
        if not responses and rec.get('usage') and rec['usage'] != state.get('previous_image_usage'):
            responses = [rec['usage']]
            requested = True
            u = responses[0]
            tokens = {'prompt':number(u.get('prompt_tokens',u.get('input_tokens'))),
                      'completion':number(u.get('completion_tokens',u.get('output_tokens'))),
                      'reasoning':number((u.get('completion_tokens_details') or {}).get('reasoning_tokens'))}
            billing['amount'] = number(u.get('total_tokens'))
            if billing['amount'] is None and tokens['prompt'] is not None and tokens['completion'] is not None:
                billing['amount'] = tokens['prompt']+tokens['completion']
        if state['channel'] == 'third_party':
            cost = _image_cost(project,rec)
            requested = requested or (cost is not None and cost != state.get('previous_image_cost'))
            billing = {'unit':'点','amount':cost}
    elif state['kind'] == 'video':
        billing = {'unit':'秒','amount':None}
        if resource:
            tid,rev = resource.split(':',1)
            rec = next((r for r in data.get('runs',{}).get(tid,[]) if r.get('rev')==rev),{})
            if rec.get('remote_task_id') or rec.get('state') == 'completed':
                billing['amount'] = number(rec.get('billing_seconds'))
    record = {'at':datetime.now().astimezone().isoformat(timespec='seconds'),
              **{k:state[k] for k in ('step','channel','model','kind')},'ok':error is None,
              'tokens':tokens,'billing':billing,'error_kind':classify_error(error) if error else None}
    if state['kind'] in {'image', 'preview'}:
        record['network_request'] = requested
        if state['kind'] == 'preview' or not requested:
            # Keep a local audit event without presenting it as a model invocation.
            record.update(kind='preview', billing={'unit':billing['unit'], 'amount':None})
    if resource: record['resource'] = resource
    store.append_usage(project,record)


def wrap_worker(fn):
    state = _current.get()
    if state is None: return fn
    context = copy_context()
    if state.get('worker_claimed'):
        context.run(_current.set,None)
        return lambda:context.run(fn)
    state['worker_claimed'] = True
    state['deferred'] = True
    def work():
        error = None
        try: return fn()
        except Exception as exc:
            error = exc
            raise
        finally: finish(state,error=error)
    return lambda: context.run(work)
