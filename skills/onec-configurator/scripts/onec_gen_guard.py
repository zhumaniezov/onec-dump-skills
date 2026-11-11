#!/usr/bin/env python3
"""Запуск скрипта движка генераторов под охраной записи (служебный, вызывает onec_gen.py).

  onec_gen_guard.py <скрипт движка> [параметры]

Перед первой записью, удалением или переименованием файла внутри корня (ONEC_GEN_ROOT)
сохраняет его исходное состояние в журнал запуска (ONEC_GEN_JOURNAL):
  backup/<путь>  — копия файла, каким он был до запуска;
  new/<путь>     — пустая метка: до запуска файла не было;
  touched.txt    — пути, которые движок менял.
Запись вне корня и разрешённых каталогов (ONEC_GEN_ALLOW: временный каталог движка) запрещена:
движок получает PermissionError и ничего не меняет за пределами корня. Это и делает пробный
запуск безопасным: корень — временная копия, к настоящим файлам доступа на запись нет.
Дочерние процессы Python, которые движок запускает тем же интерпретатором, тоже идут через охрану.
"""
import builtins
import io
import os
import runpy
import shutil
import subprocess
import sys

ROOT = os.environ.get("ONEC_GEN_ROOT") or ""
JOURNAL = os.environ.get("ONEC_GEN_JOURNAL") or ""
ALLOW = [p for p in (os.environ.get("ONEC_GEN_ALLOW") or "").split(os.pathsep) if p] + ([JOURNAL] if JOURNAL else [])
GUARD = os.path.abspath(__file__)

_open = builtins.open
_os_open = os.open
_remove = os.remove
_unlink = os.unlink
_rename = os.rename
_replace = os.replace
_mkdir = os.mkdir
_makedirs = os.makedirs
_rmdir = os.rmdir
_rmtree = shutil.rmtree
_copyfile = shutil.copyfile
_copy = shutil.copy
_copy2 = shutil.copy2
_copytree = shutil.copytree
_move = shutil.move
_Popen = subprocess.Popen
_FileIO = io.FileIO
_seen = set()


def _abs(p):
    p = os.fspath(p)
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    return os.path.abspath(p)


def _real(p):
    """Полный путь без коротких имён 8.3 (Windows) и ссылок: так пути корня и движка сравнимы."""
    return os.path.realpath(os.path.abspath(p))


def _norm(p):
    return os.path.normcase(_real(p)).rstrip("\\/")


def _inside(path, base):
    a, b = _norm(path), _norm(base)
    return a == b or a.startswith(b + os.sep)


def _deny(path, what):
    raise PermissionError("onec_gen: %s вне каталога, который меняет команда, запрещена: %s" % (what, path))


def _check(path, what="запись"):
    """Путь, который меняют: внутри корня — вернуть относительный путь, в разрешённом каталоге — None."""
    a = _abs(path)
    if ROOT and _inside(a, ROOT):
        return os.path.relpath(_real(a), _real(ROOT)).replace(os.sep, "/")
    if any(_inside(a, d) for d in ALLOW):
        return None
    _deny(a, what)


def _save(rel):
    """Сохранить исходное состояние файла rel до первой его правки."""
    if rel is None or rel in _seen or rel == ".":
        return
    _seen.add(rel)
    if not JOURNAL:
        return
    src = os.path.join(ROOT, *rel.split("/"))
    bak = os.path.join(JOURNAL, "backup", *rel.split("/"))
    new = os.path.join(JOURNAL, "new", *rel.split("/"))
    if os.path.lexists(bak) or os.path.lexists(new):         # уже сохранён (в том числе другим процессом)
        return
    if os.path.isdir(src):
        return
    if os.path.isfile(src):
        _makedirs(os.path.dirname(bak), exist_ok=True)
        with _open(src, "rb") as fi, _open(bak, "wb") as fo:
            for chunk in iter(lambda: fi.read(1 << 20), b""):
                fo.write(chunk)
        shutil.copystat(src, bak)
    else:
        _makedirs(os.path.dirname(new), exist_ok=True)
        with _open(new, "wb"):
            pass
    with _open(os.path.join(JOURNAL, "touched.txt"), "a", encoding="utf-8") as f:
        f.write(rel + "\n")


def _file(path, what="запись"):
    _save(_check(path, what))


def _tree(path, what):
    """Все файлы под каталогом path (или сам файл)."""
    a = _abs(path)
    _check(a, what)
    if os.path.isdir(a) and not os.path.islink(a):
        for dp, _dn, fns in os.walk(a):
            for fn in fns:
                _file(os.path.join(dp, fn), what)
    elif os.path.lexists(a):
        _file(a, what)


def _mirror(src, dst, what):
    """Файлы, которые появятся в dst при переносе или копировании каталога src."""
    s, d = _abs(src), _abs(dst)
    _check(d, what)
    if os.path.isdir(s):
        for dp, _dn, fns in os.walk(s):
            for fn in fns:
                _file(os.path.join(d, os.path.relpath(os.path.join(dp, fn), s)), what)
    _tree(d, what)


def _dir_ok(path):
    """Каталоги можно создавать внутри корня и на пути к нему (os.makedirs создаёт предков)."""
    a = _abs(path)
    if ROOT and _inside(ROOT, a):
        return
    _check(a, "создание каталога")


_WRITE_MODES = set("wax+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def g_open(file, mode="r", *a, **k):
    if not isinstance(file, int) and _WRITE_MODES & set(str(mode)):
        _file(file)
    return _open(file, mode, *a, **k)


class GuardedFileIO(_FileIO):
    def __init__(self, file, mode="r", *a, **k):
        if not isinstance(file, int) and _WRITE_MODES & set(str(mode)):
            _file(file)
        super().__init__(file, mode, *a, **k)


def g_os_open(path, flags, *a, **k):
    if flags & _WRITE_FLAGS and k.get("dir_fd") is None:
        _file(path)
    return _os_open(path, flags, *a, **k)


def g_remove(path, *a, **k):
    if k.get("dir_fd") is None:
        _file(path, "удаление")
    return _remove(path, *a, **k)


def g_unlink(path, *a, **k):
    if k.get("dir_fd") is None:
        _file(path, "удаление")
    return _unlink(path, *a, **k)


def g_rename(src, dst, *a, **k):
    if k.get("src_dir_fd") is not None or k.get("dst_dir_fd") is not None:
        _deny(src, "переименование относительно дескриптора")
    _mirror(src, dst, "переименование")
    _tree(src, "переименование")
    return _rename(src, dst, *a, **k)


def g_replace(src, dst, *a, **k):
    if k.get("src_dir_fd") is not None or k.get("dst_dir_fd") is not None:
        _deny(src, "переименование относительно дескриптора")
    _mirror(src, dst, "переименование")
    _tree(src, "переименование")
    return _replace(src, dst, *a, **k)


def g_mkdir(path, *a, **k):
    if k.get("dir_fd") is None:
        _dir_ok(path)
    return _mkdir(path, *a, **k)


def g_makedirs(name, *a, **k):
    _dir_ok(name)
    return _makedirs(name, *a, **k)


def g_rmdir(path, *a, **k):
    if k.get("dir_fd") is None:                       # с dir_fd вызывает только rmtree — он проверен целиком
        _check(path, "удаление каталога")
    return _rmdir(path, *a, **k)


def g_rmtree(path, *a, **k):
    if os.path.exists(path):
        _tree(path, "удаление")
    return _rmtree(path, *a, **k)


def g_copyfile(src, dst, *a, **k):
    _file(dst)
    return _copyfile(src, dst, *a, **k)


def _copy_target(src, dst):
    return os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst


def g_copy(src, dst, *a, **k):
    _file(_copy_target(src, dst))
    return _copy(src, dst, *a, **k)


def g_copy2(src, dst, *a, **k):
    _file(_copy_target(src, dst))
    return _copy2(src, dst, *a, **k)


def g_copytree(src, dst, *a, **k):
    _mirror(src, dst, "копирование")
    return _copytree(src, dst, *a, **k)


def g_move(src, dst, *a, **k):
    real = os.path.join(dst, os.path.basename(os.path.normpath(src))) if os.path.isdir(dst) else dst
    _mirror(src, real, "перенос")
    _tree(src, "перенос")
    return _move(src, dst, *a, **k)


def _same_python(exe):
    try:
        return os.path.normcase(os.path.abspath(exe)) == os.path.normcase(os.path.abspath(sys.executable))
    except (TypeError, ValueError):
        return False


class GuardedPopen(_Popen):
    """Дочерний скрипт движка тем же интерпретатором тоже запускается под охраной."""

    def __init__(self, args, *a, **k):
        if isinstance(args, (list, tuple)) and len(args) >= 2 and _same_python(args[0]) \
                and str(args[1]).lower().endswith(".py"):
            args = [args[0], GUARD] + list(args[1:])
            env = k.get("env")
            if env is not None:
                env = dict(env)
                for key in ("ONEC_GEN_ROOT", "ONEC_GEN_JOURNAL", "ONEC_GEN_ALLOW", "TMPDIR", "TEMP", "TMP"):
                    if key in os.environ:
                        env.setdefault(key, os.environ[key])
                k["env"] = env
        super().__init__(args, *a, **k)


def install():
    builtins.open = g_open
    io.open = g_open
    io.FileIO = GuardedFileIO
    os.open = g_os_open
    os.remove = g_remove
    os.unlink = g_unlink
    os.rename = g_rename
    os.replace = g_replace
    os.mkdir = g_mkdir
    os.makedirs = g_makedirs
    os.rmdir = g_rmdir
    shutil.rmtree = g_rmtree
    shutil.copyfile = g_copyfile
    shutil.copy = g_copy
    shutil.copy2 = g_copy2
    shutil.copytree = g_copytree
    shutil.move = g_move
    subprocess.Popen = GuardedPopen


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if not ROOT:
        print("onec_gen_guard: не задан ONEC_GEN_ROOT", file=sys.stderr)
        return 2
    script = os.path.abspath(sys.argv[1])
    sys.argv = [script] + sys.argv[2:]
    sys.path[0] = os.path.dirname(script)
    install()
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
