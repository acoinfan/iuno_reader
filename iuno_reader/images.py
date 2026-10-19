"""检查论文图片是否完好，坏的换成同一篇更早版本里的同名图片。

arXiv HTML 里偶尔有图片文件本身是坏的（例如 2602.04212v3 的图 1，PNG 只传了一截），
浏览器会只画出图片顶上几行，其余一片空白，而且不报加载失败，网页里的脚本发现不了，
所以在生成网页之前检查：下载每张图，PNG 把压缩数据整个解开，JPEG 看结尾标记，SVG 看结尾标签；
坏了或打不开，就依次试更早的版本（v3 → v2 → v1），用第一个完好的；都不行就保留原地址。
结果缓存在 cache/<编号>.images.json（原地址 → 实际使用的地址），同一张图只查一次。
"""
import json
import os
import re
import struct
import urllib.request
import zlib

VERSION = re.compile(r"(\d{4}\.\d{4,5})v(\d+)/")


def intact(data, url):
    """图片数据是否完整。认不出的格式算完好（只检查能检查的）。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            i, idat, ended = 8, b"", False
            while i + 8 <= len(data):
                n = struct.unpack(">I", data[i:i + 4])[0]
                kind = data[i + 4:i + 8]
                if kind == b"IDAT":
                    idat += data[i + 8:i + 8 + n]
                ended = ended or kind == b"IEND"
                i += 12 + n
            d = zlib.decompressobj()
            d.decompress(idat)
            return ended and d.eof
        if data[:2] == b"\xff\xd8":
            return data.rstrip(b"\x00").endswith(b"\xff\xd9")
        if url.lower().endswith(".svg") or data.lstrip()[:5] in (b"<?xml", b"<svg "):
            return data.rstrip().endswith(b"</svg>")
        if data[:4] == b"GIF8":
            return data.rstrip(b"\x00").endswith(b"\x3b")
    except (zlib.error, struct.error):
        return False
    return True


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "iuno_reader"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def resolve(url):
    """返回 (实际使用的地址, 是否可以缓存)。网络出错时不缓存，下次再查。"""
    candidates = [url]
    m = VERSION.search(url)
    if m:
        candidates += [url.replace(m.group(0), f"{m.group(1)}v{v}/") for v in range(int(m.group(2)) - 1, 0, -1)]
    net_error = False
    for c in candidates:
        try:
            if intact(_get(c), c):
                return c, True
        except urllib.error.HTTPError:
            continue  # 这个版本没有这张图，试下一个
        except OSError:
            net_error = True
            break
    return url, not net_error


def fix_images(doc, cache_path):
    """检查 doc 里所有图片，把坏图换成完好的旧版本地址（直接改 doc）。返回被替换的 [(原地址, 新地址)]。"""
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    changed, dirty = [], False
    for seg in doc["segments"]:
        urls = seg.get("images") or []
        for i, u in enumerate(urls):
            if u not in cache:
                new, ok = resolve(u)
                if not ok:
                    continue
                cache[u], dirty = new, True
            if cache[u] != u:
                changed.append((u, cache[u]))
                urls[i] = cache[u]
        if urls and seg.get("image"):
            seg["image"] = urls[0]
    if dirty:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    return changed
