"""Code-only updates, automatically at launch or manually from the workbench."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

REPOSITORY = 'alyunzhangu/viral-replica-workbench'
MANIFEST = 'code-update.json'
MAX_DOWNLOAD = 100 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
CHECK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 120
TOP_FILES = {'server.py', 'updater.py', 'VERSION', 'README.md', 'LICENSE',
             'start.command', 'start.bat',
             'install.sh', 'install.ps1', 'install.bat'}
# Older manifests managed these paths. Leave them alone on disk, but never
# accept them from a new release or let them prevent updating an old install.
RETIRED_FILES = {'update.command', 'update.bat', 'docs/code-updates.md'}


class UpdateError(RuntimeError):
    pass


def allowed(name):
    if not isinstance(name, str) or '\\' in name or ':' in name or '\x00' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(p.startswith('.') for p in path.parts):
        return False
    return (name in TOP_FILES or
            (len(path.parts) >= 2 and path.parts[0] == 'console' and path.suffix == '.py') or
            (len(path.parts) >= 2 and path.parts[0] == 'web' and path.suffix in
             {'.js', '.css', '.html', '.json', '.svg', '.png', '.jpg', '.ico', '.woff2'}) or
            (len(path.parts) >= 5 and path.parts[:2] == ('engine', 'skills') and
             path.parts[2] in {'jimmy-video-generator', 'seedance-storyboard-replication'} and
             path.parts[3] == 'scripts' and path.suffix == '.py'))


def safe_path(root, name, *, manifest=False, restore_legacy=False):
    if (not allowed(name) and not (manifest and name == MANIFEST)
            and not (restore_legacy and name in RETIRED_FILES)):
        raise UpdateError('更新包包含不允许覆盖的路径：' + str(name))
    path = root / name
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and p.is_relative_to(root)):
        raise UpdateError('更新路径包含符号链接：' + name)
    if path.exists() and not path.is_file():
        raise UpdateError('更新目标不是普通文件：' + name)
    return path


def validate_manifest(value):
    if not isinstance(value, dict) or value.get('format') != 1 or value.get('runtime') != 1:
        raise UpdateError('此更新需要不同的运行环境，不能作为纯代码更新安装。')
    if not re.fullmatch(r'[0-9][0-9A-Za-z._-]{0,63}', str(value.get('version', ''))):
        raise UpdateError('更新版本号无效')
    files = value.get('files')
    if not isinstance(files, dict) or not 1 <= len(files) <= 2000:
        raise UpdateError('更新文件清单无效')
    if len({n.casefold() for n in files}) != len(files):
        raise UpdateError('更新文件名存在大小写冲突')
    for name, digest in files.items():
        if not allowed(name) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise UpdateError('更新清单包含不允许的文件或无效校验值：' + str(name))
    return value


def read_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise UpdateError('无法读取更新记录：' + path.name) from exc


def atomic_write(path, data, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if mode is not None:
            temp.chmod(mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode())


def clear_bytecode(root, names):
    # Timestamp caches can survive a same-second, same-size rollback. Clear them
    # both before validating new code and after restoring the previous sources.
    for name in names:
        if not name.endswith('.py'): continue
        source = safe_path(root, name)
        cache = source.parent / '__pycache__'
        if cache.is_symlink(): raise UpdateError('代码缓存目录不能是符号链接')
        if cache.is_dir():
            for path in cache.iterdir():
                if path.name.startswith(source.stem + '.') and path.name.endswith('.pyc'):
                    path.unlink()
        source.with_suffix('.pyc').unlink(missing_ok=True)


def state_dir(root):
    path = root / 'runtime/updates'
    if (root / 'runtime').is_symlink() or path.is_symlink():
        raise UpdateError('更新备份目录不能是符号链接')
    path.mkdir(parents=True, exist_ok=True)
    return path


def download(url, limit=MAX_DOWNLOAD, *, timeout=DOWNLOAD_TIMEOUT):
    """Bound the entire request, including DNS, redirects and trickling reads.

    A socket timeout alone restarts with each read and may not bound DNS. Only
    this daemon performs network I/O; it never writes files or installs code.
    """
    if timeout <= 0:
        raise UpdateError('检查更新超时，稍后启动时会重试。')
    done, cancelled = threading.Event(), threading.Event()
    result = []

    def fetch():
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'ViralReplica-CodeUpdater/1',
                                                         'Accept': 'application/vnd.github+json'})
            ctx = ssl.create_default_context()
            try:
                import certifi
                ctx.load_verify_locations(certifi.where())
            except ImportError:
                pass
            with urllib.request.urlopen(request, context=ctx, timeout=timeout) as response:
                data = bytearray()
                while not cancelled.is_set():
                    chunk = response.read(min(65536, limit + 1 - len(data)))
                    if not chunk:
                        result.append(bytes(data))
                        return
                    data.extend(chunk)
                    if len(data) > limit:
                        raise UpdateError('更新下载超过大小限制')
        except Exception as exc:
            result.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=fetch, name='code-update-download', daemon=True)
    thread.start()
    try:
        if not done.wait(timeout):
            raise UpdateError('下载更新超时，稍后启动时会重试。')
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError('发布源尚未提供此版本的代码更新，请稍后检查。') from None
        if exc.code in (403, 429):
            raise UpdateError('更新服务暂时限流，请稍后重试。') from None
        raise UpdateError(f'下载更新失败（HTTP {exc.code}），当前代码未改变。') from None
    except TimeoutError as exc:
        raise UpdateError('下载更新超时，稍后启动时会重试。') from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError('无法连接更新服务，请检查网络后重试。') from exc
    finally:
        cancelled.set()


def stage_archive(raw, manifest):
    validate_manifest(manifest)
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 10000 or sum(m.file_size for m in members) > MAX_EXPANDED:
                raise UpdateError('更新包解压后超过大小限制')
            names = [m.filename for m in members]
            if len(set(names)) != len(names):
                raise UpdateError('更新包含有重复文件路径')
            roots = {n.split('/')[0] for n in names}
            if len(roots) != 1:
                raise UpdateError('更新包目录结构无效')
            prefix = next(iter(roots)) + '/'
            embedded = json.loads(archive.read(prefix + MANIFEST))
            if embedded != manifest:
                raise UpdateError('更新清单与检查时的版本不一致，请重新检查。')
            for name, digest in manifest['files'].items():
                item = archive.getinfo(prefix + name)
                if stat.S_ISLNK(item.external_attr >> 16) or item.is_dir():
                    raise UpdateError('更新包包含链接或无效文件：' + name)
                data = archive.read(item)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise UpdateError('更新文件校验失败：' + name)
                if name.endswith('.py'):
                    try: compile(data, name, 'exec')
                    except (SyntaxError, ValueError) as exc:
                        raise UpdateError('更新代码语法检查失败：' + name) from exc
                files[name] = data
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise UpdateError('更新包不完整或格式错误，当前代码未改变。') from exc
    if not {'server.py', 'updater.py', 'VERSION', 'web/app.js'} <= files.keys():
        raise UpdateError('更新包缺少核心文件')
    if files['VERSION'].decode().strip() != manifest['version']:
        raise UpdateError('更新包版本号不一致')
    return files


def restore(root, backup):
    if not re.fullmatch('[0-9a-f]{32}', str(backup)):
        raise UpdateError('备份编号无效')
    folder = state_dir(root) / 'backups' / backup
    record = read_json(folder / 'record.json')
    if not record or not isinstance(record.get('files'), dict):
        raise UpdateError('找不到可用的代码备份')
    # Preflight the entire backup before changing any files.
    changes = []
    for name, saved in record['files'].items():
        target = safe_path(root, name, manifest=True, restore_legacy=True)
        data = None
        if saved['exists']:
            source = folder / 'files' / name
            if source.is_symlink(): raise UpdateError('备份文件不能是符号链接')
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != saved['sha256']:
                raise UpdateError('备份校验失败：' + name)
        changes.append((target, data, saved.get('mode')))
    for target, data, mode in changes:
        if data is None: target.unlink(missing_ok=True)
        else: atomic_write(target, data, mode)
    clear_bytecode(root, record['files'])


def recover(root):
    pending = state_dir(root) / 'pending.json'
    transaction = read_json(pending)
    if not transaction: return False
    if transaction.get('state') != 'committed':
        restore(root, transaction['backup'])
        if transaction.get('state') == 'rolling_back':
            (state_dir(root) / 'latest.json').unlink(missing_ok=True)
    else:
        write_json(state_dir(root) / 'latest.json', {'backup': transaction['backup']})
    pending.unlink()
    return True


def install(root, raw, manifest, *, validate=lambda: ''):
    root = Path(root).resolve()
    recover(root)
    files = stage_archive(raw, manifest)
    previous = read_json(root / MANIFEST)
    if isinstance(previous, dict) and isinstance(previous.get('files'), dict):
        previous = {**previous, 'files': {name: digest for name, digest in previous['files'].items()
                                         if name not in RETIRED_FILES}}
    old_names = set(validate_manifest(previous)['files']) if previous else set()
    files[MANIFEST] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    names = sorted(set(files) | old_names)
    targets = {name: safe_path(root, name, manifest=True) for name in names}
    state = state_dir(root)
    backup = uuid.uuid4().hex
    folder = state / 'backups' / backup
    record = {'files': {}}
    for name, target in targets.items():
        saved = {'exists': target.exists()}
        if target.exists():
            data = target.read_bytes()
            saved.update(mode=stat.S_IMODE(target.stat().st_mode), sha256=hashlib.sha256(data).hexdigest())
            atomic_write(folder / 'files' / name, data)
        record['files'][name] = saved
    write_json(folder / 'record.json', record)
    pending = state / 'pending.json'
    write_json(pending, {'state': 'installing', 'backup': backup})
    try:
        for name, target in targets.items():
            if name not in files:
                target.unlink(missing_ok=True)
            else:
                mode = record['files'][name].get('mode', 0o755 if name.endswith(('.sh', '.command')) else 0o644)
                atomic_write(target, files[name], mode)
        clear_bytecode(root, targets)
        problem = validate()
        if problem: raise UpdateError('更新后代码检查未通过：' + problem)
        write_json(pending, {'state': 'committed', 'backup': backup})
        write_json(state / 'latest.json', {'backup': backup})
        pending.unlink()
        return backup
    except (Exception, KeyboardInterrupt):
        restore(root, backup)
        pending.unlink(missing_ok=True)
        raise


def rollback(root):
    state = state_dir(root)
    latest = read_json(state / 'latest.json')
    if not latest: raise UpdateError('还没有可恢复的更新备份')
    write_json(state / 'pending.json', {'state': 'rolling_back', 'backup': latest['backup']})
    restore(root, latest['backup'])
    (state / 'pending.json').unlink(missing_ok=True)
    (state / 'latest.json').unlink(missing_ok=True)


def validate_installed(root):
    code = """
import importlib
from pathlib import Path
# Import validation must not initialize caches, models, temp paths or shims.
if Path('console/runtime_env.py').is_file():
    from console import runtime_env
    runtime_env.apply = lambda: None
import server
for path in sorted(Path('console').rglob('*.py')):
    importlib.import_module('.'.join(path.with_suffix('').parts).removesuffix('.__init__'))
"""
    try:
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=60)
        return '' if result.returncode == 0 else '新版本无法在当前运行环境中加载：' + (result.stderr or result.stdout)[-1000:]
    except (OSError, subprocess.TimeoutExpired):
        return '新版本启动检查未完成'


class Updater:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.active = 0
        self.phase = 'idle'
        self.message = ''
        self.release = None

    def status(self):
        with self.lock:
            version = (self.root / 'VERSION').read_text().strip() if (self.root / 'VERSION').exists() else '旧版本'
            return {'phase': self.phase, 'message': self.message, 'current_version': version,
                    'repository': REPOSITORY,
                    'can_rollback': (self.root / 'runtime/updates/latest.json').exists(),
                    'available': {'sha': self.release['sha'], 'version': self.release['manifest']['version']}
                    if self.release else None}

    def applying(self):
        with self.lock:
            return self.phase in {'downloading', 'installing', 'rolling_back'}

    @contextlib.contextmanager
    def activity(self):
        with self.lock:
            if self.applying() or self.phase == 'restarting':
                raise UpdateError('工作台正在更新，请等待完成后刷新。')
            self.active += 1
        try: yield
        finally:
            with self.lock: self.active -= 1

    def check(self, *, timeout=CHECK_TIMEOUT):
        with self.lock:
            if self.applying() or self.phase in {'checking', 'restarting'}:
                raise UpdateError('已有更新操作正在进行')
            self.phase, self.message, self.release = 'checking', '正在检查更新…', None
        try:
            deadline = time.monotonic() + timeout
            commit = json.loads(download(f'https://api.github.com/repos/{REPOSITORY}/commits/main', 2_000_000,
                                         timeout=deadline - time.monotonic()))
            sha = commit.get('sha', '')
            if not re.fullmatch('[0-9a-f]{40}', sha): raise UpdateError('发布版本标识无效')
            manifest = validate_manifest(json.loads(download(
                f'https://raw.githubusercontent.com/{REPOSITORY}/{sha}/{MANIFEST}', 2_000_000,
                timeout=deadline - time.monotonic())))
            current = read_json(self.root / MANIFEST)
            with self.lock:
                if current == manifest:
                    self.message = '当前已是最新发布版本。'
                else:
                    self.release = {'sha': sha, 'manifest': manifest}
                    self.message = '发现新版本，只更新程序代码。'
                self.phase = 'idle'
        except Exception as exc:
            with self.lock: self.phase, self.message = 'error', str(exc)
            raise UpdateError(str(exc)) from exc
        return self.status()

    def start(self, sha='', *, busy=lambda: False, validate=lambda: '', rollback_only=False, restart=True):
        with self.lock:
            if self.applying() or self.phase in {'checking', 'restarting'}:
                raise UpdateError('已有更新操作正在进行')
            if self.active: raise UpdateError('还有请求正在处理，请完成后更新。')
            if busy(): raise UpdateError('还有生成或分析任务正在运行，请完成后更新。')
            if not rollback_only and (not self.release or sha != self.release['sha']):
                raise UpdateError('更新版本已失效，请重新检查更新。')
            release = self.release
            self.phase, self.message = ('rolling_back', '正在恢复上一个代码版本…') if rollback_only else ('downloading', '正在下载代码更新…')

        def worker():
            try:
                if rollback_only:
                    rollback(self.root)
                else:
                    raw = download(f'https://api.github.com/repos/{REPOSITORY}/zipball/{release["sha"]}')
                    with self.lock: self.phase, self.message = 'installing', '正在校验、备份并更新代码…'
                    install(self.root, raw, release['manifest'], validate=validate)
                with self.lock:
                    self.release = None
                    self.phase = 'restarting' if restart else 'done'
                    self.message = '代码已更新，正在重启工作台，请稍候刷新。' if restart else '代码已更新，请双击启动文件打开工作台。'
            except Exception as exc:
                with self.lock:
                    self.phase, self.message = 'error', '更新未完成：' + str(exc)
        thread = threading.Thread(target=worker, name='code-update', daemon=True)
        thread.start()
        return self.status()


def acquire_lock(root):
    """Held for the server lifetime; also excludes other updater processes."""
    stream = (state_dir(root) / 'install.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            stream.write(b'0'); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise UpdateError('工作台或其他更新程序正在运行，请先关闭后重试。') from None


def legacy_server_running(root):
    try: pid = int((root / 'runtime/server.pid').read_text().strip())
    except (ValueError, FileNotFoundError): return False
    if not 0 < pid < 2**31: return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


def auto_update(root):
    """Best effort only: every outcome lets the launcher continue."""
    try:
        if (root / '.git').exists():
            print('跳过自动更新：此目录由 Git 管理，请使用 git pull。')
            return 0
        if os.environ.get('VRC_NO_AUTO_UPDATE') == '1':
            print('跳过自动更新：已设置 VRC_NO_AUTO_UPDATE=1。')
            return 0
        if legacy_server_running(root):
            print('跳过自动更新：工作台仍在运行。')
            return 0
        with acquire_lock(root):
            if not (root / 'server.py').is_file():
                raise UpdateError('未找到工作台程序，请在原安装目录启动。')
            recover(root)
            service = Updater(root)
            if not service.check()['available']:
                print('已是最新版本')
                return 0
            release = service.release
            raw = download(f'https://api.github.com/repos/{REPOSITORY}/zipball/{release["sha"]}',
                           timeout=DOWNLOAD_TIMEOUT)
            install(root, raw, release['manifest'], validate=lambda: validate_installed(root))
            print('已更新到 ' + release['manifest']['version'] + ' 版本')
    except (Exception, KeyboardInterrupt) as exc:
        # Do not print tracebacks, English transport errors or multiple lines.
        reason = ' '.join(str(exc).split()) if isinstance(exc, UpdateError) else '检查或安装未完成'
        print('跳过自动更新：' + reason[:300].rstrip('。；; ') + '。继续启动本地版本。')
    return 0


def main():
    parser = argparse.ArgumentParser(description='只更新工作台代码，保留项目、Key、模型和依赖')
    parser.add_argument('--rollback', action='store_true', help='恢复上一次更新前的代码')
    parser.add_argument('--auto', action='store_true', help='启动时自动检查更新，失败时继续使用本地版本')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.auto:
        return auto_update(root)
    try:
        if not (root / 'server.py').is_file():
            raise UpdateError('请在原工作台目录（与 server.py 同级）运行更新器。')
        if legacy_server_running(root):
            raise UpdateError('原工作台进程仍在运行，请先关闭启动窗口再升级。')
        # Old versions predate the process lock: refuse updates while their port is live.
        port = 8765
        port = int(os.environ.get('PORT') or os.environ.get('VRC_PORT') or port)
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise UpdateError('请先关闭正在运行的工作台窗口，再运行更新器。')
        lock = acquire_lock(root)
        try:
            recover(root)
            if args.rollback:
                rollback(root); print('已恢复更新前的代码，项目和素材不变。'); return 0
            service = Updater(root)
            result = service.check()
            if not result['available']:
                print(result['message']); return 0
            print('将更新到 ' + result['available']['version'] + '。项目、Key、模型和依赖保留。')
            if input('输入 Y 开始更新：').strip().lower() != 'y':
                print('已取消'); return 0
            service.start(result['available']['sha'], restart=False, validate=lambda: validate_installed(root))
            while service.applying(): time.sleep(.2)
            result = service.status(); print(result['message'])
            return 1 if result['phase'] == 'error' else 0
        finally: lock.close()
    except (UpdateError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print('未能更新：' + str(exc)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
