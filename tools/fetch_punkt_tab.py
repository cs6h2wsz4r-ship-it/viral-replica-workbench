"""Download and validate NLTK punkt_tab without the NLTK downloader."""
from __future__ import annotations

import argparse
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
URL = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip"
MAX_SIZE = 50 * 1024 * 1024


def extract_archive(archive: zipfile.ZipFile, dest: Path) -> None:
    """Validate the entire archive before creating anything in the destination."""
    total = 0
    for entry in archive.infolist():
        name = entry.filename
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                or not path.parts or path.parts[0] != "punkt_tab"):
            raise ValueError(f"压缩包包含不安全路径：{name}")
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"压缩包包含非普通文件：{name}")
        if not entry.is_dir() and (len(path.parts) < 2 or
                                   (path.suffix not in (".tab", ".txt") and path.name != "README")):
            raise ValueError(f"压缩包包含不允许的文件：{name}")
        total += entry.file_size
        if total > MAX_SIZE:
            raise ValueError("压缩包解压后的总大小超过 50MB")
        target = dest / "tokenizers" / path
        # Never follow a pre-existing symlink out of the destination.
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError(f"目标路径包含符号链接：{name}")
    archive.extractall(dest / "tokenizers")


def verify(dest: Path) -> None:
    import nltk
    from nltk.tokenize import PunktTokenizer

    previous = nltk.data.path[:]
    try:
        # Restrict lookup to this destination, not a usable installation elsewhere.
        nltk.data.path[:] = [str(dest.resolve())]
        nltk.data.find("tokenizers/punkt_tab/english/")
        if PunktTokenizer("english").tokenize("Hello world. This is a test.") != ["Hello world.", "This is a test."]:
            raise ValueError("英文断句验证失败")
    finally:
        nltk.data.path[:] = previous


def fetch(dest: Path) -> None:
    try:
        verify(dest)
    except (LookupError, OSError, ValueError):
        pass
    else:
        print("punkt_tab 就绪（已有可用数据，跳过下载）")
        return

    temp_root = ROOT / "runtime/temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="punkt-download-", dir=temp_root) as tmp:
        archive_path = Path(tmp) / "punkt_tab.zip"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(URL, timeout=120) as response, archive_path.open("wb") as output:
                    shutil.copyfileobj(response, output)
                break
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f"punkt_tab 下载失败（已重试 2 次）：{exc}") from exc
                print(f"punkt_tab 下载失败，准备第 {attempt + 1} 次重试：{exc}", file=sys.stderr)
        # Validate in staging first; a bad download must not damage existing data.
        staged = Path(tmp) / "nltk"
        with zipfile.ZipFile(archive_path) as archive:
            extract_archive(archive, staged)
        verify(staged)
        with zipfile.ZipFile(archive_path) as archive:
            extract_archive(archive, dest)
        verify(dest)
    print("punkt_tab 就绪")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并验证 WhisperX 所需的 NLTK punkt_tab")
    parser.add_argument("--dest", type=Path, default=ROOT / "runtime/models/nltk", help="NLTK 数据目标目录")
    args = parser.parse_args()
    try:
        fetch(args.dest)
    except Exception as exc:
        print(f"punkt_tab 准备失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
