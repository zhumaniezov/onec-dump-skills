"""Общие пути для скриптов скилла: корень выгрузки и место индекса.

Индекс лежит в папке проекта: <проект>/.onec/index.sqlite.
  - выгрузка в подпапке cf, src/cf, src, config, dump (C:\\projects\\alfa\\cf) —
    проект это папка над ней (C:\\projects\\alfa), индекс переживает перевыгрузку;
  - выгрузка прямо в папке проекта — индекс в ней же, в .onec.
Переопределить: ONEC_INDEX_DIR (общий каталог для всех индексов) или ключ --db.
"""
import hashlib
import os

# подпапки, в которых часто лежит выгрузка внутри рабочей папки проекта
ROOT_SUBDIRS = ("cf", os.path.join("src", "cf"), "src", "config", "dump")


def project_dir(root):
    """Папка проекта для выгрузки root: над cf / src/cf / src / config / dump, иначе сама root."""
    root = os.path.abspath(root).rstrip("\\/")
    name = os.path.basename(root).lower()
    parent = os.path.dirname(root)
    if name == "cf" and os.path.basename(parent).lower() == "src":
        return os.path.dirname(parent)
    if name in ("cf", "src", "config", "dump"):
        return parent
    return root


def default_db(root):
    d = os.environ.get("ONEC_INDEX_DIR")
    if d:
        root = os.path.abspath(root)
        name = os.path.basename(project_dir(root)) or "dump"
        h = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()[:8]
        return os.path.join(d, "%s-%s.sqlite" % (name, h))
    return os.path.join(project_dir(root), ".onec", "index.sqlite")


def old_locations(root):
    """Где индекс мог лежать в прежних версиях (для подсказки удалить)."""
    out = [os.path.join(os.path.abspath(root), ".onec", "index.sqlite")]
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else (os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"))
    if base:
        out.append(os.path.join(base, "onec-dump"))
    return out


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
