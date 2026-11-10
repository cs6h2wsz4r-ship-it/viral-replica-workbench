"""Final-edit timing, including read-only inference for scripts saved before 24.19."""
import math


def target_seconds(task: dict) -> float:
    explicit = task.get('target_seconds')
    if explicit is not None:
        value = float(explicit)
    else:
        value = float(task.get('source_end') or 0) - float(task.get('source_start') or 0)
        estimate = task.get('estimate')
        if task.get('kind') == 'speaker' and estimate is not None and float(estimate) > value + 1:
            value = float(estimate)
        # Very old manually authored scripts may have no source interval.
        if value <= 0:
            value = float(task.get('duration') or 0)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('这一段的成片时长必须是大于 0 的秒数，请检查原片起止时间')
    return value
