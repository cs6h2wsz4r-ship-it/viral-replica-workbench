"""Report local alignment models without downloading or loading model weights."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / "runtime/temp"
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def offline_audit(event, args):
    if event.startswith("socket."):
        raise RuntimeError(f"本地模型检查禁止网络访问：{event}")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.name == "console.env":
            raise RuntimeError("本地模型检查禁止读取 console.env")
        mode, flags = args[1], args[2]
        if ((isinstance(mode, str) and any(c in mode for c in "wax+")) or
                (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))):
            if not path.is_relative_to(TEMP):
                raise RuntimeError(f"本地模型检查仅允许写临时目录，禁止写入：{path}")


def main():
    from console.align_models import report
    labels = {"zh": "中文", "en": "英文"}
    states = {"ok": "已安装", "missing": "未安装", "broken": "文件不完整或已损坏，请重新下载后替换"}
    for model in report():
        print(f"{model['language']} = {model['status']}（{labels[model['language']]}对齐模型：{states[model['status']]}）")
        print(f"  模型：{model['model']}（{model['type']}）")
        print(f"  期望路径：{model['path']}")
        if model["status"] != "ok":
            if model.get("download_url"):
                print(f"  下载来源：{model['download_url']}")
            if model.get("size"):
                print(f"  完整大小：{model['size']} 字节")
            if model.get("detail"):
                print(f"  说明：{model['detail']}")
    return 0


if __name__ == "__main__":
    TEMP.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(TEMP)
    sys.addaudithook(offline_audit)
    raise SystemExit(main())
