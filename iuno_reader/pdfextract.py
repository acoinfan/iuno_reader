"""PDF → 和网页拆分（extract.py）同样格式的 doc.json。没有 arXiv / ar5iv 网页版的论文才走这条路。

准确度低于网页版，渲染时会醒目标出：PDF 只有排好版的文字块，段落、节标题、阅读顺序都要靠版面猜。
  - 阅读顺序：按栏排（通栏的块把一页分成几段，每段里先左栏后右栏）。
  - 去掉页眉页脚（页面上下边缘、在好几页重复出现的短文字、页码）、arXiv 侧边的竖排水印、图里的小字。
  - 节标题：短、粗体或字号偏大，并且有编号或是常见节名（Introduction、Conclusion……）。
  - 段落：跨栏、跨页没说完的段落拼回去；行尾断词借本地词典判断要不要留连字符。
  - 图、表：按图注位置把图（图注上方）或表（表题下方）裁成图片；行间公式也裁成图片，不翻译。
  - 参考文献：拆成条目，猜出标题和年份，供引用统计和知识库对应用；正文里的引用没有链接。
最后算覆盖率：拆进片段的正文文字 / 页面上正文字号的全部文字；低于 0.9 或认不出节标题时记警告。
"""
import base64
import re
from collections import Counter

import pymupdf

from .extract import _clean, split_sentences

_NUM_HEAD = re.compile(r"^((\d+(\.\d+)*)\.?|([A-H](\.\d+)*)\.?|([IVX]{1,5})\.)\s+(\S.*)$")
_NAMED = re.compile(r"^(abstract|introduction|related work|background|preliminaries|method(s|ology)?|experiments?|results|"
                    r"discussions?|conclusions?|limitations?|broader impacts?|impact statement|acknowledge?ments?|"
                    r"references|bibliography|appendix|appendices|supplementary material|future work|summary)\b", re.I)
_CAPTION = re.compile(r"^(Figure|Fig\.|Table|Tab\.)\s*(\d+)\s*[.:|]", re.I)
_BOLD_FONT = re.compile(r"bold|medi|semibold|heavy|black|\.B$|-B$|CMBX", re.I)


def _is_bold(span):
    return bool(span["flags"] & 16) or bool(_BOLD_FONT.search(span["font"]))


def _join_spans(spans):
    """同一行里换字体的地方 PyMuPDF 会切成几段，段与段之间有空隙就补一个空格（免得出现「6.2Probing」）。"""
    out, prev = "", None
    for sp in spans:
        t = sp["text"]
        if prev is not None and t and out and not out.endswith(" ") and not t.startswith(" ") \
                and sp["bbox"][0] - prev["bbox"][2] > 0.8:
            out += " "
        out += t
        prev = sp
    return out


def _runin(lines):
    """段首嵌着的粗体标题（Nature 一类期刊：「RESULTS The successor representation An animal’s …」）。
    返回 (标题, 余下正文)；没有就返回 ("", "")。以句号结尾的粗体（「Superposition.」这种段落小标题）不算。"""
    spans = [sp for ln in lines for sp in ln["spans"]]
    i = 0
    while i < len(spans) and (_is_bold(spans[i]) or not spans[i]["text"].strip()):
        i += 1
    head = re.sub(r"\s+", " ", _join_spans(spans[:i])).strip()
    rest = re.sub(r"\s+", " ", " ".join(sp["text"] for sp in spans[i:])).strip()
    if not (3 <= len(head) <= 120) or not rest or head.endswith((".", ":", ",")) or not re.match(r"[A-Z]", rest):
        return "", ""
    return head, rest


class Block:
    __slots__ = ("page", "bbox", "lines", "size", "bold", "text", "kind", "keep", "lead_bold")

    def __init__(self, page, bbox, lines):
        self.page, self.bbox, self.lines = page, pymupdf.Rect(bbox), lines
        chars = Counter()
        bold = 0
        for ln in lines:
            for sp in ln["spans"]:
                n = len(sp["text"].strip())
                chars[round(sp["size"] * 2) / 2] += n
                bold += n if _is_bold(sp) else 0
        total = sum(chars.values()) or 1
        self.size = chars.most_common(1)[0][0] if chars else 0
        self.bold = bold / total > 0.6
        self.text = " ".join(" ".join(sp["text"] for sp in ln["spans"]).strip() for ln in lines).strip()
        self.kind, self.keep = "", True
        first = next((sp for ln in lines for sp in ln["spans"] if sp["text"].strip()), None)
        self.lead_bold = bool(first and _is_bold(first))


class _Words:
    """行尾断词：「repre-」+「sentation」拼起来；「in-」+「context」保留连字符。有本地词典时查词典，没有就直接拼。"""

    def __init__(self, root):
        self.db = None
        try:
            from .dictionary import open_db
            self.db = open_db(root) if root else None
        except Exception:
            self.db = None

    def has(self, w):
        return bool(self.db and self.db.execute("select 1 from d where word = ? collate nocase", (w,)).fetchone())

    def join(self, left, right):
        a = re.search(r"([A-Za-z]+)-$", left)
        b = re.match(r"([a-z]+)", right)
        if not a or not b:
            return left + " " + right
        if not self.db:
            return left[:-1] + right
        wa, wb = a.group(1), b.group(1)
        if self.has(f"{wa}-{wb}"):
            return left + right
        if self.has(wa + wb):
            return left[:-1] + right
        return left + right if (self.has(wa) and self.has(wb)) else left[:-1] + right


def _line_text(lines, words):
    out = ""
    for ln in lines:
        t = re.sub(r"\s+", " ", _join_spans(ln["spans"]) if ln["spans"] and "bbox" in ln["spans"][0]
                   else "".join(sp["text"] for sp in ln["spans"])).strip()
        if not t:
            continue
        out = t if not out else (words.join(out, t) if out.endswith("-") else out + " " + t)
    return out


def _png_uri(page, rect, dpi):
    pix = page.get_pixmap(clip=rect, dpi=int(dpi))
    return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()


def _order(blocks, width):
    """一页里的文字块按阅读顺序排：通栏块把页面分成几段，每段先左栏后右栏。"""
    full, left, right = [], [], []
    for b in blocks:
        x0, x1 = b.bbox.x0, b.bbox.x1
        if x0 < width * 0.45 and x1 > width * 0.55:
            full.append(b)
        elif x0 >= width * 0.45:
            right.append(b)
        else:
            left.append(b)
    if not right:  # 单栏页：直接按上下顺序
        return sorted(blocks, key=lambda b: (round(b.bbox.y0), b.bbox.x0))
    out, prev = [], -1e9
    for f in sorted(full, key=lambda b: b.bbox.y0) + [None]:
        y = f.bbox.y0 if f else 1e9
        out += sorted([b for b in left if prev <= b.bbox.y0 < y], key=lambda b: b.bbox.y0)
        out += sorted([b for b in right if prev <= b.bbox.y0 < y], key=lambda b: b.bbox.y0)
        if f:
            out.append(f)
        prev = y
    return out


def _bib_entry(text, n):
    text = _clean(text)
    tag = ""
    m = re.match(r"^\[(\d+)\]\s*", text)
    if m:
        tag, text = m.group(1), text[m.end():]
    parts = [p.strip() for p in re.split(r"(?<=[a-z0-9)\]])\.\s+(?=[A-Z0-9\"“])", text) if p.strip()]
    title = ""
    if len(parts) >= 2:
        title = parts[2] if re.fullmatch(r"\(?(19|20)\d{2}[a-z]?\)?", parts[1]) and len(parts) > 2 else parts[1]
    if not title or re.search(r"\b[A-Z]\.,|\s&\s", title) or title.count(",") > 3:
        # 期刊格式「Daw, N.D., Niv, Y. & Dayan, P. Uncertainty-based competition … Nat. Neurosci. 8, …」：
        # 作者名缩写也以句点结尾，标题从「句点 + 大写开头、后面跟着小写词」的地方开始
        m2 = re.search(r"\.\s+([A-Z][\w\-’']*(?:\s+[a-z][\w\-’']*).*?)(?:\.\s+[A-Z]|$)", text)
        if m2:
            title = m2.group(1)
    year = re.search(r"\b(19|20)\d{2}\b", text)
    arx = re.search(r"(?:arXiv[:\s]*(?:preprint\s+arXiv:)?\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})", text, re.I)
    return {"tag": tag or str(n), "authors": parts[0] if parts else "", "title": title.strip(" .\"“”"),
            "year": year.group(0) if year else "", "text": text, "links": re.findall(r"https?://\S+", text),
            "arxiv": arx.group(1) if arx else ""}


def pdf_title(path):
    """只读第一页：上半页字号最大的文字当标题（清单里没写标题的会议版 PDF，靠它去 arXiv 找对应版本）。"""
    try:
        page = pymupdf.open(path)[0]
    except Exception:
        return ""
    spans = [(round(sp["size"], 1), sp["text"].strip(), b["bbox"][1]) for b in page.get_text("dict")["blocks"]
             if b["type"] == 0 for ln in b["lines"] if abs(ln["dir"][0] - 1) < 0.01 for sp in ln["spans"]
             if sp["text"].strip() and b["bbox"][1] < page.rect.height * 0.5]
    if not spans:
        return ""
    big = max(s for s, t, y in spans if len(t) > 2) if any(len(t) > 2 for _, t, _ in spans) else 0
    return _clean(" ".join(t for s, t, y in spans if s == big))


def extract_pdf(path, pid, root=None, dpi=110):
    pdf = pymupdf.open(path)
    words = _Words(root)
    pages, raw = [], []
    for pno, page in enumerate(pdf):
        d = page.get_text("dict")
        blocks, images = [], []
        for b in d["blocks"]:
            if b["type"] == 1:
                images.append(pymupdf.Rect(b["bbox"]))
                continue
            lines = [ln for ln in b["lines"] if abs(ln["dir"][0] - 1) < 0.01 and "".join(s["text"] for s in ln["spans"]).strip()]
            if lines:  # 竖排的行（arXiv 侧边水印）丢掉
                blocks.append(Block(pno, b["bbox"], lines))
        pages.append((page, blocks, images))
        raw += blocks

    # 正文字号；页眉页脚：页面上下 7% 以内、在 3 页以上重复出现（数字归一）或者只是页码
    sizes = Counter()
    for b in raw:
        sizes[b.size] += len(b.text)
    body = sizes.most_common(1)[0][0] if sizes else 10
    edge = Counter()
    for page, blocks, _ in pages:
        h = page.rect.height
        for b in blocks:
            if (b.bbox.y1 < h * 0.07 or b.bbox.y0 > h * 0.93) and len(b.text) < 150:
                edge[re.sub(r"\d+", "#", b.text)] += 1
    for page, blocks, images in pages:
        h = page.rect.height
        for b in blocks:
            at_edge = b.bbox.y1 < h * 0.07 or b.bbox.y0 > h * 0.93
            if at_edge and (edge[re.sub(r"\d+", "#", b.text)] >= 3 or re.fullmatch(r"[\divxlc\s\-–]+", b.text, re.I)):
                b.keep = False
            elif any(im.intersects(b.bbox) and (im & b.bbox).get_area() > 0.6 * b.bbox.get_area() for im in images):
                b.keep = False  # 图片里的文字

    # 标题：第一页字号最大的块
    first = [b for b in pages[0][1] if b.keep] if pages else []
    title_blocks = []
    if first:
        big = max(b.size for b in first if b.bbox.y0 < pages[0][0].rect.height * 0.5)
        title_blocks = [b for b in first if b.size == big and b.bbox.y0 < pages[0][0].rect.height * 0.5]
    title = _clean(" ".join(b.text for b in title_blocks)) or pid
    for b in title_blocks:
        b.keep = False

    segs, bib_texts, head_texts = [], [], []
    section, subsection, state = "", "", "front"  # front：标题到摘要之间（作者、单位），不收
    found_heads, n_fig, n_eq, n_fn = 0, 0, 0, 0
    last_top = None  # 上一个正文节的编号，用来排除表格里「1 N」这类假标题
    pending = None  # 还没说完的段落（可能跨栏、跨页接着写）

    def set_head(name, m):
        """登记一个节或小节标题；编号倒退、跳得太远时返回 False，当正文处理。"""
        nonlocal state, section, subsection, found_heads, last_top
        low = (m.group(7) if m else name).lower()
        top = (not m) or bool(m.group(2) and "." not in m.group(2)) or bool(m.group(6)) \
            or bool(m.group(4) and "." not in m.group(4))
        if m and m.group(2) and state not in ("refs", "appendix"):
            n = int(m.group(2).split(".")[0])
            if top:
                if (last_top is None and n > 3) or (last_top is not None and not 1 <= n - last_top <= 2):
                    return False
                last_top = n
            elif last_top is not None and n not in (last_top, last_top + 1):
                return False
            elif last_top is not None and n == last_top + 1:
                last_top = n  # 小节先出现、大节标题没认出来
        flush()
        found_heads += 1
        head_texts.append(name)
        if low.startswith(("references", "bibliography")):
            state = "refs"
        elif low.startswith(("acknowledg", "author contributions", "competing", "reprints")):
            state, section = "skip", "__skip__"
        elif state in ("refs", "appendix") and top:
            state = "appendix"
            section, subsection = (name if name.lower().startswith("appendix") else "Appendix " + name), ""
        elif top:
            state, section, subsection = "body", name, ""
        else:
            if state in ("front", "abstract"):
                state = "body"
            subsection = name
        return True

    def flush():
        nonlocal pending
        if pending:
            en = _clean(pending["en"])
            if en:
                pending["en"], pending["sentences"] = en, split_sentences(en)
                segs.append(pending)
            pending = None

    def add(kind, b, en, **extra):
        flush()
        en = _clean(en)
        d = {"id": f"p{b.page + 1}.{len(segs) + 1}", "kind": kind, "section": section, "subsection": subsection,
             "en": en, "sentences": split_sentences(en) if kind != "equation" else []}
        d.update(extra)
        segs.append(d)

    for page, blocks, images in pages:
        width, height = page.rect.width, page.rect.height
        ordered = [b for b in _order(blocks, width) if b.keep]
        for i, b in enumerate(ordered):
            text = _line_text(b.lines, words)
            if not text:
                continue
            short = len(text) < 100 and len(b.lines) <= 2
            m = _NUM_HEAD.match(text)
            is_head = short and (b.bold or b.size >= body + 1) and not text.endswith((",", ";")) and (
                (m and not re.search(r"\.\s+\S", m.group(7)) and m.group(7)[:1].isupper()
                 and re.search(r"[A-Za-z]{3,}", m.group(7))) or _NAMED.match(text))
            # 「Abstract」单独一行，或者段首就是「Abstract—」「Abstract.」
            ab = re.match(r"^abstract\s*[—–\-.:]?\s*", text, re.I)
            if ab and state == "front":
                flush()
                state, section, subsection = "abstract", "Abstract", ""
                rest = text[ab.end():]
                if rest and not short:
                    add("abstract", b, rest)
                continue
            if is_head and set_head(_clean(text), m):
                continue
            if b.lead_bold and state != "front":  # 段首嵌着的粗体标题
                head, rest = _runin(b.lines)
                caps = re.match(r"^([A-Z][A-Z &\-]{3,}[A-Z])(?:\s+(.*))?$", head) if head else None
                if caps and set_head(caps.group(1).title(), None):
                    if caps.group(2) and state != "skip":
                        subsection = caps.group(2)
                    text = _line_text([{"spans": [{"text": rest}]}], words) if state != "skip" else ""
                elif head and not caps and state in ("body", "appendix") and len(head) >= 15 and len(head.split()) >= 3:
                    flush()
                    subsection = head
                    head_texts.append(head)
                    text = rest
                if not text:
                    continue
            if state != "refs" and b.size < body - 0.5 and re.match(r"^(\[1\]|1\.)\s+[A-Z][\w'’\-]+,?\s", text) \
                    and page.number >= len(pdf) // 3:
                flush()
                state = "refs"  # 没有 References 标题、直接是编号参考文献（Nature 一类期刊）
            if state == "skip":
                continue
            if state == "refs":
                bib_texts.append(text)
                continue
            if state == "front":
                prose = len(text) >= 250 and len(re.findall(r"\b(the|of|and|to|we|in)\b", text, re.I)) >= 4
                if not prose:
                    b.kind = "front"  # 作者、单位：有意跳过，覆盖率不算它
                    continue
                if not any(s["kind"] == "abstract" for s in segs):
                    state, section = "abstract", "Abstract"  # 没写「Abstract」的摘要：第一页第一段像正文的长段
                    add("abstract", b, text)
                    state, section = "body", "Introduction"  # 摘要后面没有标题的正文，按引言算
                    continue
                state, section = "body", "Introduction"
            elif state == "abstract" and pending is None and any(s["kind"] == "abstract" for s in segs) \
                    and page.number > 0:
                state, section = "body", "Introduction"
            cap = _CAPTION.match(text) or (b.lead_bold and re.match(r"^(Figure|Fig\.|Table)\s*(\d+)\s+[A-Z(]", text))
            if cap:
                is_table = cap.group(1).lower().startswith("tab")
                # 图：图注上方到上一个文字块之间；表：表题下方到下一个文字块之间。只在同一栏里找
                col = [o for o in ordered if o is not b and o.bbox.x1 > b.bbox.x0 + 5 and o.bbox.x0 < b.bbox.x1 - 5]
                if is_table:
                    below = [o.bbox.y0 for o in col if o.bbox.y0 > b.bbox.y1 + 2 and (o.size >= body - 0.5 or _CAPTION.match(o.text))]
                    rect = pymupdf.Rect(b.bbox.x0 - 4, b.bbox.y1 + 1, b.bbox.x1 + 4, min(below + [height * 0.94]))
                else:
                    above = [o.bbox.y1 for o in col if o.bbox.y1 < b.bbox.y0 - 2 and o.size >= body - 0.5]
                    rect = pymupdf.Rect(b.bbox.x0 - 4, max(above + [height * 0.06]), b.bbox.x1 + 4, b.bbox.y0 - 1)
                img = _png_uri(page, rect, dpi) if rect.height > 20 and rect.width > 40 else None
                n_fig += 1
                add("table" if is_table else "caption", b, text, image=img, images=[img] if img else [])
                continue
            if b.size < body - 1.2:
                if b.bbox.y0 > height * 0.7 and re.match(r"^(\d{1,2}|[*†‡§¶])\s*\S", text):
                    n_fn += 1
                    add("footnote", b, re.sub(r"^(\d{1,2}|[*†‡§¶])\s*", "", text), footnote=n_fn)
                continue  # 其余小字多是图表里的标注
            letters = sum(c.isalpha() for c in text)
            mathy = len(re.findall(r"[=∑∫∏√≤≥≈∈∀∃→←∂∇±×·^_{}|]", text))
            if len(text) < 300 and (letters < 0.55 * len(text.replace(" ", "")) or mathy >= 3 and re.search(r"\(\d+\)\s*$", text)):
                n_eq += 1
                rect = pymupdf.Rect(b.bbox.x0 - 3, b.bbox.y0 - 2, b.bbox.x1 + 3, b.bbox.y1 + 2)
                eqtag = re.search(r"\((\d+)\)\s*$", text)
                add("equation", b, "", math="", image=_png_uri(page, rect, dpi * 1.4),
                    eqtag=f"({eqtag.group(1)})" if eqtag else "", text=text)
                continue
            kind = "abstract" if state == "abstract" else "para"
            starts_low = bool(re.match(r"^[a-z(\[,;]", text))
            if pending and pending["kind"] == kind and (starts_low or not re.search(r"[.!?:][\"')\]]*$", pending["en"])):
                pending["en"] = words.join(pending["en"], text) if pending["en"].endswith("-") else pending["en"] + " " + text
                continue
            flush()
            pending = {"id": f"p{b.page + 1}.{len(segs) + 1}", "kind": kind, "section": section,
                       "subsection": subsection, "en": text}
        # 一页结束不 flush：段落可能接着写到下一页
    flush()

    # 参考文献条目：带 [n] 的按编号切；作者-年份格式的按块切（PyMuPDF 一般一条一块）
    joined = " ".join(bib_texts)
    if re.match(r"^\[\d+\]", joined):
        items = [x for x in re.split(r"\s(?=\[\d+\]\s)", joined) if x.strip()]
    else:
        items = []
        for t in bib_texts:
            if items and (re.match(r"^[a-z]", t) or not re.search(r"[.)]$", items[-1])):
                items[-1] += " " + t
            else:
                items.append(t)
    bib = {f"bib.{i + 1}": _bib_entry(t, i + 1) for i, t in enumerate(items)}

    # 覆盖率：页面上正文字号的文字（PDF 原文，不经过上面的拼段）逐词核对，有多少词进了片段、标题或参考文献
    def bag(t):  # 连字符两边都去掉（原文「repre- sentation」和拼好的「representation」要算同一个词）
        return Counter(re.findall(r"[a-z]{3,}", re.sub(r"-\s*", "", t.lower())))
    want = Counter()
    for b in raw:
        if b.keep and b.kind != "front" and abs(b.size - body) <= 0.6:
            want += bag(b.text)
    got = bag(" ".join([s["en"] for s in segs] + [s.get("text", "") for s in segs] + bib_texts + head_texts))
    total = sum(want.values())
    coverage = round(sum(min(n, got[w]) for w, n in want.items()) / total, 3) if total else 0.0
    warnings = []
    if coverage < 0.9:
        warnings.append(f"覆盖率只有 {coverage:.0%}，可能漏了段落")
    if found_heads < 3:
        warnings.append("几乎认不出节标题，分节可能不对")
    if not any(s["kind"] == "abstract" for s in segs):
        warnings.append("没找到摘要")
    if not bib:
        warnings.append("没找到参考文献")
    return {"arxiv_id": pid, "title": title, "source": "pdf", "base_url": "", "segments": segs, "bib": bib,
            "pdf": {"file": path.rsplit("/", 1)[-1], "pages": len(pdf), "coverage": coverage,
                    "headings": found_heads, "figures": n_fig, "equations": n_eq, "warnings": warnings}}
