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


def _read_doc(data, addr):
    """Прочитать документ (цепочку страниц) по адресу addr."""
    hdr = data[addr:addr + BLOCK_HDR]
    if len(hdr) < BLOCK_HDR or hdr[:2] != b"\r\n" or hdr[-2:] != b"\r\n":
        raise ValueError("неверный заголовок блока по адресу 0x%x" % addr)
    doc_size = int(hdr[2:10], 16)
    page_size = int(hdr[11:19], 16)
    next_addr = int(hdr[20:28], 16)
    out = bytearray()
    pos = addr + BLOCK_HDR
    remaining = doc_size
    while True:
        take = min(page_size, remaining)
        out += data[pos:pos + take]
        remaining -= take
        if remaining <= 0 or next_addr == END:
            break
        hdr = data[next_addr:next_addr + BLOCK_HDR]
        page_size = int(hdr[11:19], 16)
        pos = next_addr + BLOCK_HDR
        next_addr = int(hdr[20:28], 16)
    return bytes(out)


def parse(data):
    """-> список (имя, created(8), modified(8), содержимое-байты)."""
    if len(data) < FILE_HDR + BLOCK_HDR:
        raise ValueError("файл слишком короткий для контейнера 1С")
    toc = _read_doc(data, FILE_HDR)
    entries = []
    for i in range(0, len(toc) - 11, 12):
        a_hdr, a_body, _ = struct.unpack("<III", toc[i:i + 12])
        if a_hdr == END:
            continue
        h = _read_doc(data, a_hdr)
        name = h[20:].decode("utf-16-le").split("\x00", 1)[0]
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


def _main(argv):
    import os
    if len(argv) < 2 or argv[1] not in ("list", "unpack", "pack"):
        print("usage: v8container.py list <Form.bin> | unpack <Form.bin> <dir> | pack <dir> <Form.bin>")
        return 2
    if argv[1] == "list":
        for name, c, m, body in parse(open(argv[2], "rb").read()):
            print("%-12s %8d байт" % (name, len(body)))
    elif argv[1] == "unpack":
        os.makedirs(argv[3], exist_ok=True)
        for name, c, m, body in parse(open(argv[2], "rb").read()):
            with open(os.path.join(argv[3], name), "wb") as f:
                f.write(body)
    else:
        ents = []
        for name in sorted(os.listdir(argv[2])):
            ents.append((name, b"\x00" * 8, b"\x00" * 8, open(os.path.join(argv[2], name), "rb").read()))
        open(argv[3], "wb").write(build(ents))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
