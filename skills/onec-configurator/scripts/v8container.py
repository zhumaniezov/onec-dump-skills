#!/usr/bin/env python3
"""Чтение и запись контейнера 1С (формат Form.bin обычных форм, .cf/.epf-блоки).

Только стандартная библиотека. Записи пишутся «как есть» (без deflate) —
для Form.bin платформа 8.3.27 требует именно несжатые записи.

Формат (см. docs/ordinary-forms.md в проекте):
  заголовок файла, 16 байт : int32 next_free (0x7fffffff), int32 page_size,
                             int32 версия хранилища, int32 резерв
  блок документа           : "\r\n%08x %08x %08x \r\n" (31 байт) -
                             doc_size, page_size, next_page; затем данные,
                             цепочка страниц до next_page == 0x7fffffff
  оглавление               : документ из троек int32 (addr_заголовка, addr_тела, 0x7fffffff)
  заголовок элемента       : 8 байт создан, 8 байт изменён, 4 байта резерв,
                             имя UTF-16LE с нулевым терминатором
"""
import struct
import sys

END = 0x7FFFFFFF
BLOCK_HDR = 31
FILE_HDR = 16


def _block_header(data, addr):
    """Заголовок страницы "\r\n%08x %08x %08x \r\n" -> (doc_size, page_size, next_addr)."""
    if addr < FILE_HDR or addr + BLOCK_HDR > len(data):
        raise ValueError("адрес страницы 0x%x вне файла (размер файла %d): контейнер обрезан или повреждён"
                         % (addr, len(data)))
    hdr = data[addr:addr + BLOCK_HDR]
    if (hdr[:2] != b"\r\n" or hdr[29:31] != b"\r\n"
            or hdr[10:11] != b" " or hdr[19:20] != b" " or hdr[28:29] != b" "):
        raise ValueError("неверный заголовок страницы по адресу 0x%x" % addr)
    try:
        return int(hdr[2:10], 16), int(hdr[11:19], 16), int(hdr[20:28], 16)
    except ValueError:
        raise ValueError("неверные числа в заголовке страницы по адресу 0x%x" % addr) from None


def _read_doc(data, addr):
    """Прочитать документ (цепочку страниц) по адресу addr.

    Проверяется всё, что можно проверить без платформы: заголовки страниц, адреса в
    пределах файла, фактическое наличие заявленного числа байт, отсутствие петель.
    У страниц-продолжений поле размера документа равно 0 (так пишет платформа).
    """
    doc_size, page_size, next_addr = _block_header(data, addr)
    out = bytearray()
    pos = addr + BLOCK_HDR
    remaining = doc_size
    seen = {addr}
    while True:
        take = min(page_size, remaining)
        if pos + take > len(data):
            raise ValueError("документ по адресу 0x%x обрезан: заявлено %d байт, в файле есть %d"
                             % (addr, doc_size, len(out) + max(0, len(data) - pos)))
        out += data[pos:pos + take]
        remaining -= take
        if remaining <= 0:
            break
        if next_addr == END:
            raise ValueError("документ по адресу 0x%x короче заявленного: %d байт из %d"
                             % (addr, len(out), doc_size))
        if next_addr in seen:
            raise ValueError("петля в цепочке страниц документа 0x%x" % addr)
        seen.add(next_addr)
        _cont_size, page_size, nxt = _block_header(data, next_addr)
        if page_size == 0:
            raise ValueError("пустая страница-продолжение 0x%x в документе 0x%x" % (next_addr, addr))
        pos = next_addr + BLOCK_HDR
        next_addr = nxt
    return bytes(out)


def parse(data):
    """-> список (имя, created(8), modified(8), содержимое-байты). ValueError при повреждении."""
    if len(data) < FILE_HDR + BLOCK_HDR:
        raise ValueError("файл слишком короткий для контейнера 1С")
    toc = _read_doc(data, FILE_HDR)
    if len(toc) % 12:
        raise ValueError("оглавление контейнера повреждено: %d байт не кратно 12" % len(toc))
    entries = []
    names = set()
    for i in range(0, len(toc), 12):
        a_hdr, a_body, _ = struct.unpack("<III", toc[i:i + 12])
        if a_hdr == END:
            continue
        h = _read_doc(data, a_hdr)
        if len(h) < 22:
            raise ValueError("заголовок записи по адресу 0x%x слишком короткий" % a_hdr)
        try:
            name = h[20:].decode("utf-16-le").split("\x00", 1)[0]
        except UnicodeDecodeError:
            raise ValueError("имя записи по адресу 0x%x не читается" % a_hdr) from None
        if not name or name in names:
            raise ValueError("пустое или повторное имя записи: %r" % name)
        names.add(name)
        body = _read_doc(data, a_body) if a_body != END else b""
        entries.append((name, h[:8], h[8:16], body))
    return entries


def _doc_bytes(payload, page_size, doc_size=None):
    """Собрать документ: заголовок блока + страницы (одна цепочка)."""
    size = len(payload) if doc_size is None else doc_size
    pages = [payload[i:i + page_size] for i in range(0, max(len(payload), 1), page_size)]
    return size, pages


def build(entries, page_size=512):
    """entries: список (имя, created(8), modified(8), body). Возвращает bytes.

    Для простоты и надёжности каждый документ укладывается в одну «страницу»
    размером с документ (так делает и платформа для документов больше страницы).
    """
    blocks = []  # (payload, page_size)

    def doc(payload, exact=False):
        # как у платформы: заголовок записи — страница ровно по размеру,
        # оглавление и тела — не меньше page_size
        ps = len(payload) if exact else max(page_size, len(payload))
        return payload, ps

    # сначала элементы: [заголовок, тело]...; потом оглавление; адреса считаем после раскладки
    items = []
    for name, created, modified, body in entries:
        hdr = created + modified + b"\x00\x00\x00\x00" + name.encode("utf-16-le") + b"\x00\x00"
        items.append((doc(hdr, exact=True), doc(body)))
    toc_len = 12 * len(entries)
    toc = doc(b"\x00" * toc_len)

    def blen(d):
        return BLOCK_HDR + d[1]

    addr = FILE_HDR
    toc_addr = addr
    addr += blen(toc)
    addrs = []
    for h, b in items:
        a_h = addr
        addr += blen(h)
        a_b = addr
        addr += blen(b)
        addrs.append((a_h, a_b))
    toc_payload = b"".join(struct.pack("<III", a, b, END) for a, b in addrs)

    out = bytearray()
    out += struct.pack("<IIII", END, page_size, 2, 0)

    def emit(payload, ps):
        out.extend(b"\r\n%08x %08x %08x \r\n" % (len(payload), ps, END))
        out.extend(payload)
        out.extend(b"\x00" * (ps - len(payload)))

    emit(toc_payload, toc[1])
    for h, b in items:
        emit(*h)
        emit(*b)
    return bytes(out)


def safe_entry_name(name):
    """Имя записи как имя файла: без каталогов, диска, служебных и пустых имён."""
    bad = set('\\/:*?"<>|') | {chr(c) for c in range(32)}
    if (not name or name in (".", "..") or any(ch in bad for ch in name)
            or name != name.strip() or name.endswith(".") or len(name) > 128):
        raise ValueError("недопустимое имя записи для распаковки: %r" % name)
    return name


def unpack(data, out_dir):
    """Распаковать записи в новые файлы внутри out_dir. Существующие файлы не перезаписываются."""
    import os
    ents = parse(data)
    names = [safe_entry_name(n) for n, _c, _m, _b in ents]       # все имена проверяются до записи
    if len({n.lower() for n in names}) != len(names):
        raise ValueError("имена записей совпадают без учёта регистра")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.realpath(out_dir)
    targets = []
    for n in names:
        t = os.path.join(base, n)
        if os.path.dirname(os.path.realpath(t)) != base:
            raise ValueError("запись %r указывает за пределы каталога" % n)
        if os.path.lexists(t):
            raise ValueError("файл уже существует, не перезаписываю: %s" % t)
        targets.append(t)
    for t, (_n, _c, _m, body) in zip(targets, ents):
        with open(t, "xb") as f:
            f.write(body)
    return targets


def _main(argv):
    import os
    cmd = argv[1] if len(argv) > 1 else ""
    if not ((cmd == "list" and len(argv) == 3) or (cmd in ("unpack", "pack") and len(argv) == 4)):
        print("usage: v8container.py list <Form.bin> | unpack <Form.bin> <новый-каталог> | pack <каталог> <новый-Form.bin>")
        return 2
    try:
        if cmd == "list":
            with open(argv[2], "rb") as f:
                for name, _c, _m, body in parse(f.read()):
                    print("%-12s %8d байт" % (name, len(body)))
        elif cmd == "unpack":
            with open(argv[2], "rb") as f:
                for t in unpack(f.read(), argv[3]):
                    print(t)
        else:
            ents = []
            for name in sorted(os.listdir(argv[2])):
                with open(os.path.join(argv[2], safe_entry_name(name)), "rb") as f:
                    ents.append((name, b"\x00" * 8, b"\x00" * 8, f.read()))
            with open(argv[3], "xb") as f:                         # не перезаписывать существующий файл
                f.write(build(ents))
    except (OSError, ValueError) as ex:
        print("ОШИБКА: %s" % ex)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
