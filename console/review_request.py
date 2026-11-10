"""Bound review evidence before inference; never discard reference photos/cells."""
from __future__ import annotations

import json
import math

from . import llm, media
from .gateway.errors import LLMError

MAX_IMAGES = 32
MAX_BYTES = 3_000_000
MAX_FRAMES = 20
MIN_FRAMES = 12


class ReviewMessages(list):
    """Local-only marker: JSON serialization and successful request bytes stay identical."""
    review_budget = True


def metrics(messages):
    body = {'messages': messages}
    return dict(image_count=len(llm._request_images(body)),
                request_bytes=len(json.dumps(body, ensure_ascii=False).encode('utf-8')))


def _fits(messages):
    # Account for either wire format and reserve room for model/settings fields.
    # Do not resolve a route or read configuration just to budget local evidence.
    native = {'contents': [{'role': 'user', 'parts': llm._to_gemini_parts(messages)}]}
    size = max(metrics(messages)['request_bytes'], len(json.dumps(native, ensure_ascii=False).encode('utf-8')))
    return metrics(messages)['image_count'] <= MAX_IMAGES and size + 4096 <= MAX_BYTES


def enforce(messages):
    if not _fits(messages):
        exc = LLMError('请求太大：保留参考素材后仍超过检查上限，请减少本段素材或缩短段落。', kind='too_large')
        exc.error_detail = {**metrics(messages), 'http_status': None, 'error_type': 'review_request_limit',
                            'message': '检查请求超过 32 张图片或 3 MB 上限'}
        raise exc
    return ReviewMessages(messages)


def uniform(values, count):
    if len(values) <= count:
        return list(values)
    return [values[round(i * (len(values) - 1) / (count - 1))] for i in range(count)]


def last_frame(duration, fps=None):
    rate = float(fps or 25)
    if not math.isfinite(rate) or rate <= 0:
        rate = 25
    # Seeking to the container's end is EOF, not the last picture.
    timestamp = max(0., duration - 1 / rate)
    # extract_frame seeks with millisecond precision; rounding up can land after
    # the last PTS at fractional frame rates and incorrectly produce an empty file.
    return math.floor(timestamp * 1000 + 1e-6) / 1000


def segment_times(duration, fps=None):
    if duration <= 10:
        return [i / 2 for i in range(math.ceil(duration * 2))]
    end = last_frame(duration, fps)
    return [round(i * end / (MAX_FRAMES - 1), 6) for i in range(MAX_FRAMES)]


def bounded(build, times):
    times = uniform(sorted(set(times)), MAX_FRAMES)
    request = build(times, 540)
    if _fits(request):
        return ReviewMessages(request)
    request = build(times, 432)
    if _fits(request):
        return ReviewMessages(request)
    for count in range(len(times) - 1, min(MIN_FRAMES, len(times)) - 1, -1):
        request = build(uniform(times, count), 432)
        if _fits(request):
            return ReviewMessages(request)
    return enforce(request)


class Frames:
    """Encode each sampled frame once, including across budget retries."""
    def __init__(self, clip, folder, *, prefix=''):
        self.clip, self.folder, self.prefix = clip, folder, prefix
        self.cache = {}

    def parts(self, times, width):
        parts = []
        for second in times:
            key = (second, width)
            if key not in self.cache:
                path = self.folder / f'frame-{len(self.cache):03d}.jpg'
                # Retain the old short-segment extraction and encoding exactly.
                frame = (media.extract_frame(self.clip, second, path) if width == 540 else
                         media.extract_frame(self.clip, second, path, width=width))
                self.cache[key] = llm.image_part(frame)
            parts.append(dict(type='text', text=f'{self.prefix}t={second:.1f}s'))
            parts.extend(self.cache[key])
        return parts
