"""Чтение PNG — только для тестов.

Движок пишет PNG своим мини-писателем на zlib+struct и читать их не умеет.
Тесту паритета нужно сравнивать КАРТИНКУ, а не байты файла: zlib на разных
машинах жмёт одинаковые данные по-разному, и побайтовое сравнение .png
падало бы на CI из-за другой версии библиотеки, а не из-за разъехавшихся
пикселей. Поэтому эталон читается обратно в RGBA и сравнивается попиксельно.

Понимает 8-bit RGB/RGBA, все пять фильтров строк — этого хватает и для
наших файлов, и для картинок, сделанных другими инструментами.
"""

import struct
import zlib


def read_png(path):
    """-> (width, height, rows), где rows — список строк кортежей (r,g,b,a)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("не PNG: %s" % path)

    pos = 8
    width = height = depth = ctype = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # 4 длина + 4 тег + тело + 4 crc
        if tag == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break

    if depth != 8 or ctype not in (2, 6):
        raise ValueError("поддерживаются только 8-bit RGB/RGBA: %s" % path)
    channels = 3 if ctype == 2 else 4
    stride = width * channels

    raw = zlib.decompress(bytes(idat))
    rows = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        # Восстановление строки по фильтру (обратная операция к записи).
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
            elif ftype != 0:
                raise ValueError("неизвестный фильтр %d в %s" % (ftype, path))
        rows.append(_to_rgba(line, channels))
        prev = line
    return width, height, rows


def _to_rgba(line, channels):
    if channels == 4:
        return [tuple(line[x:x + 4]) for x in range(0, len(line), 4)]
    return [(line[x], line[x + 1], line[x + 2], 255)
            for x in range(0, len(line), 3)]
