"""Общие пути для скриптов скилла: корень выгрузки и место индекса.

Индекс по умолчанию хранится вне выгрузки, чтобы переживать её удаление и
перевыгрузку и не попадать в Git:
  Windows:       %LOCALAPPDATA%\\onec-dump\\index\\<папка>-<хеш пути>.sqlite
  Linux/macOS:   $XDG_CACHE_HOME (или ~/.cache)/onec-dump/index/...
Переопределить каталог: переменная ONEC_INDEX_DIR; конкретный файл: ключ --db.
"""
import hashlib
import os

# подпапки, в которых часто лежит выгрузка внутри рабочей папки проекта
ROOT_SUBDIRS = ("cf", os.path.join("src", "cf"), "src", "config", "dump")


def index_dir():
    d = os.environ.get("ONEC_INDEX_DIR")
    if d:
        return d
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "onec-dump", "index")


def default_db(root):
    root = os.path.abspath(root)
    name = os.path.basename(root.rstrip("\\/")) or "dump"
    parent = os.path.basename(os.path.dirname(root))
    if name.lower() in ("cf", "src", "config", "dump") and parent:
        name = parent + "-" + name
    h = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()[:8]
    return os.path.join(index_dir(), "%s-%s.sqlite" % (name, h))


def legacy_db(root):
    """Расположение индекса до версии 2.6 — внутри выгрузки."""
    return os.path.join(root, ".onec", "index.sqlite")


def is_extension(root):
    try:
        with open(os.path.join(root, "Configuration.xml"), "rb") as f:
            return b"ConfigurationExtensionPurpose" in f.read(20000)
    except OSError:
        return False


def find_root(start):
    """Корень выгрузки: папка с Configuration.xml — сама, её родители или подпапки
    cf, src/cf, src, config, dump у стартовой папки. None, если не найден."""
    p = os.path.abspath(start)
    if os.path.isfile(p):
        p = os.path.dirname(p)
    for sub in ROOT_SUBDIRS:
        c = os.path.join(p, sub)
        if os.path.isfile(os.path.join(c, "Configuration.xml")) and not is_extension(c):
            if not os.path.isfile(os.path.join(p, "Configuration.xml")):
                return c
    while True:
        if os.path.isfile(os.path.join(p, "Configuration.xml")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up
