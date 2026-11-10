"""Names and readable errors at presentation boundaries; wire identifiers stay intact."""
from __future__ import annotations

import re


def technical_detail(value: object) -> str:
    return re.sub(r'jimmy|吉米', 'third-party', str(value), flags=re.I)


def user_message(value: object) -> str:
    text = str(value)
    lower = text.lower()
    third_party = bool(re.search(r'jimmy|吉米|third-party', text, re.I))
    if 'unrecognized status' in lower:
        return '三方渠道返回生成中状态，继续等待'
    if third_party and not re.search(r'[\u4e00-\u9fff]', text):
        if any(word in lower for word in ('insufficient', 'balance', 'quota')):
            return '三方渠道额度不足，请检查账户余额'
        if any(word in lower for word in ('unauthorized', 'authentication', 'forbidden', 'api key')):
            return '三方渠道密钥无效或没有权限，请在设置里检查'
        if 'timeout' in lower or 'timed out' in lower:
            return '三方渠道请求超时，请稍后恢复查询任务状态'
        if 'cancel' in lower:
            return '三方渠道任务已取消'
        return '三方渠道任务失败，请检查配置或稍后重试'
    # Configuration keys and filenames are identifiers, not names shown in prose.
    text = text.replace('吉米币', '点').replace('吉米', '三方渠道')
    return re.sub(r'(?<![\w/.-])jimmy(?![\w/.-])', '三方渠道', text, flags=re.I)


def third_party_message(value: object) -> str:
    """Remote script failures may omit the channel name entirely."""
    text = str(value)
    if not re.search(r'[\u4e00-\u9fff]', text):
        if 'upload' in text.lower():
            return '三方渠道参考素材上传失败，请检查网络后重试'
        return user_message('third-party ' + text)
    return user_message(text)
