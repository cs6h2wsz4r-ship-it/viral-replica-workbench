"""On-demand photo descriptions and product-dependent story questions."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from . import gateway, llm, store
from .channel_text import user_message

PERSON_KEYS = ('gender_age','face','hair','outfit','body')
PRODUCT_KEYS = ('category','look','parts','how_used')


def _photo(data, entry, directory):
    asset = next((a for a in data.get('assets',[]) if a['id']==entry.get('asset_id') and a.get('type')=='image'),None)
    path = (directory/(asset or {}).get('file','')).resolve()
    if not asset or not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('请先从素材库选一张照片')
    return path


def product_sig(data, directory, product=None):
    product = product if product is not None else (data.get('replacements') or {}).get('product') or {}
    try:
        photo = store.sha256_file(_photo(data,product,directory))
    except (OSError,ValueError):
        photo = 'missing'
    return hashlib.sha256(json.dumps([product.get('asset_id'),photo,product.get('description') or ''],ensure_ascii=False).encode()).hexdigest()


def story_stale(data, directory):
    product = (data.get('replacements') or {}).get('product') or {}
    return bool(product.get('story_checks_sig') and product['story_checks_sig'] != product_sig(data,directory))


def _target(data, kind, source_id):
    rep = data.get('replacements') or {}
    if kind=='product': return rep.get('product') or {}
    return next((c for c in rep.get('characters',[]) if c.get('source_id')==source_id),{})


def _save(pid, data, kind, source_id, entry):
    before = _target(data,kind,source_id)
    def commit(current):
        if _target(current,kind,source_id) != before:
            raise ValueError('这张卡片刚刚改过，请重试')
        rep = current.setdefault('replacements',{})
        if kind=='product': rep['product']=entry
        else:
            rows = rep.setdefault('characters',[])
            index = next((i for i,c in enumerate(rows) if c.get('source_id')==source_id),len(rows))
            if index==len(rows): rows.append(entry)
            else: rows[index]=entry
    return store.update(pid,commit)


def identify(pid, body):
    try:
        data = store.load(pid); directory = store.project_dir(pid)
        kind, cid = body.get('kind'), body.get('source_id')
        if kind not in ('character','product'): raise ValueError('请选择人物或商品照片')
        original = next((c for c in (data.get('breakdown') or {}).get('characters',[]) if c.get('id')==cid),{})
        if kind=='character' and not original and not re.fullmatch(r'N[1-9]\d*',str(cid)):
            raise ValueError('没有这个人物，请刷新页面')
        entry = {**copy.deepcopy(_target(data,kind,cid)),**copy.deepcopy(body.get('entry') or {})}
        if kind=='character': entry.update(source_id=cid,action='add' if str(cid).startswith('N') else entry.get('action','replace'))
        photo = _photo(data,entry,directory)
        keys = PERSON_KEYS if kind=='character' else PRODUCT_KEYS
        context = {k:original.get(k,'') for k in ('appearance','outfit','role')} if kind=='character' else {'商品说明':entry.get('description','')}
        prompt = ('识别人物外形' if kind=='character' else '识别商品外形和用法')+'。中文，只写照片里看得见的内容，不猜测功能，每项不超过30字。只输出JSON：'+json.dumps(dict.fromkeys(keys,''),ensure_ascii=False)
        parts = [{'type':'text','text':prompt+'\n原有说明：'+json.dumps(context,ensure_ascii=False)}]+llm.image_part(photo)
        look, _ = gateway.reason_checked('cast',[{'role':'user','content':parts}],project=pid,max_tokens=1200)
        if not isinstance(look,dict) or any(k not in look or not isinstance(look[k],str) or len(look[k])>30 for k in keys):
            raise ValueError('照片识别结果不完整，请重试')
        look = {k:look[k] for k in keys}
        description = '；'.join(v.strip() for v in look.values() if v.strip())
        if not description: raise ValueError('照片里没有识别到清楚的外形，请换张照片重试')
        suggested = bool(str(entry.get('description') or '').strip())
        entry['look']=look
        if not suggested: entry['description']=description
        result = _save(pid,data,kind,cid,entry)
        store.log(pid,('商品' if kind=='product' else str(cid))+'照片外形识别完成')
        return dict(project=store.load(pid),look=look,description=description,suggested=suggested)
    except Exception as exc:
        store.log(pid,'照片识别没完成：'+user_message(exc),'warn')
        raise


def story_messages(data, entry, photo):
    pack = data.get('breakdown') or {}
    context = {k:pack.get(k,[]) for k in ('productMoments','dialogue','overlays')}
    context['beats'] = [{k:b.get(k) for k in ('start','end','summary')} for b in pack.get('beats') or []]
    context['新商品说明']=entry.get('description','')
    prompt = '检查换商品后剧情是否成立。最多5条，只列推动剧情、卖点依赖的功能，不列外观差异，不推测新商品支持。没有依赖则返回空数组。只输出JSON，以下占位内容按实际输入填写：{"checks":[{"id":"k1","feature":"<剧情依赖的功能>","where":"<原片时间区间与剧情依据>","question":"<新商品是否支持该功能的问题>"}]}\n'
    parts = [{'type':'text','text':prompt+json.dumps(context,ensure_ascii=False)}]+llm.image_part(photo)
    return [{'role':'user','content':parts}]


def validate_story(value):
    checks=value.get('checks') if isinstance(value,dict) else None
    if not isinstance(checks,list) or len(checks)>5 or any(not isinstance(c,dict) or any(not isinstance(c.get(k),str) or not c[k].strip() for k in ('id','feature','where','question')) for c in checks) or len({c['id'] for c in checks})!=len(checks):
        raise ValueError('剧情检查结果不完整，请重试')
    return [{**{k:c[k] for k in ('id','feature','where','question')},'answer':'','note':''} for c in checks]


def check_story(pid, body):
    try:
        data = store.load(pid); directory = store.project_dir(pid)
        entry = {**copy.deepcopy(_target(data,'product',None)),**copy.deepcopy(body.get('entry') or {})}
        photo = _photo(data,entry,directory)
        checked_sig = product_sig(data,directory,entry)
        value, _ = gateway.reason_checked('cast',story_messages(data,entry,photo),project=pid,max_tokens=2200)
        entry['story_checks']=validate_story(value)
        entry['story_checks_sig']=checked_sig
        _save(pid,data,'product',None,entry)
        store.log(pid,f'剧情检查完成：{len(entry["story_checks"])} 条需要确认')
        return dict(project=store.load(pid))
    except Exception as exc:
        store.log(pid,'剧情检查没完成：'+user_message(exc),'warn')
        raise
