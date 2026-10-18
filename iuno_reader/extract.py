"""把 arXiv HTML（LaTeXML 生成）拆成可逐段、逐句翻译的片段。

输出 doc.json：
{
  "arxiv_id": ..., "title": ..., "source": "arxiv-html", "base_url": ...,
  "segments": [
    {"id": "S2.p3", "kind": "para|abstract|caption|footnote|item|equation|table",
     "section": "2 Preliminaries ...", "subsection": "Metrics",
     "en": "完整英文", "sentences": ["句1", "句2", ...],
     "image": "图片 URL（仅图注）", "math": "LaTeX（仅行间公式）"}
  ]
}

约定：
- 行内公式写成 $...$（取 LaTeXML 的 alttext），翻译时必须原样保留。
- 脚注在正文里替换成占位符 {fn1}、{fn2}……，脚注内容另起一个 footnote 片段。
- 等宽字体（如 [ANSWER]）写成 `...`，粗体写成 **...**。
"""
import json
import re
from bs4 import BeautifulSoup, NavigableString, Tag

ABBREV = {
    "e.g.", "i.e.", "et al.", "al.", "Fig.", "Figs.", "Eq.", "Eqs.", "Sec.", "Secs.",
    "vs.", "cf.", "approx.", "resp.", "etc.", "No.", "Tab.", "App.", "Ref.", "Refs.",
    "Dr.", "Mr.", "Ms.", "Prof.", "St.", "Inc.", "Jr.", "Sr.", "viz.", "w.r.t.",
}


_FLOAT_RE = re.compile(r"^\s*(Figure|Fig\.|Table|Tab\.)\s*(\d+)", re.I)
FLOAT_KINDS = ("caption", "table", "footnote")


def float_ref_pattern(seg):
    """图注、表题、脚注片段 → 用来在正文里找「谁引用了它」的正则；不是这类片段返回 None。"""
    if seg["kind"] == "footnote":
        return re.compile(re.escape("{fn%s}" % seg.get("footnote")))
    if seg["kind"] not in ("caption", "table"):
        return None
    m = _FLOAT_RE.match(seg["en"])
    if not m:
        return None
    word = r"(?:Tables?|Tab\.)" if m.group(1).lower().startswith("tab") else r"(?:Figures?|Figs?\.)"
    return re.compile(rf"\b{word}\s*{m.group(2)}\b")


def referenced_floats(segments, main_ids):
    """返回被 main_ids 这些正文片段引用到的图、表、脚注的 id。"""
    main = [s for s in segments if s["id"] in main_ids and s["kind"] not in FLOAT_KINDS]
    out = set()
    for f in segments:
        pat = float_ref_pattern(f)
        if pat and any(pat.search(s["en"]) for s in main):
            out.add(f["id"])
    return out


class Ctx:
    def __init__(self):
        self.footnotes = []  # (n, Tag)


def _text_of(node, ctx, in_note=False):
    """递归取文本：公式→$alttext$，脚注→占位符，等宽→`..`，粗体→**..**。"""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    cls = node.get("class", [])
    if node.name == "math":
        alt = (node.get("alttext") or "").strip()
        if node.get("display") == "block":
            return f" $${alt}$$ "
        return f"${alt}$"
    if "ltx_note" in cls:
        if "ltx_note_frontmatter" in cls or in_note:
            return ""
        content = node.find(class_="ltx_note_content")
        ctx.footnotes.append(content)
        return "{fn%d}" % len(ctx.footnotes)
    if "ltx_note_mark" in cls or "ltx_tag_note" in cls:
        return ""
    if node.name in ("script", "style", "annotation", "annotation-xml"):
        return ""
    inner = "".join(_text_of(c, ctx, in_note) for c in node.children)
    if "ltx_font_typewriter" in cls and inner.strip():
        return f"`{inner.strip()}`"
    if "ltx_font_bold" in cls and inner.strip() and node.name != "h2":
        return f"**{inner.strip()}**"
    return inner


def _clean(s):
    s = s.replace(" ", " ").replace("​", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    return s


_MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$")


def split_sentences(text):
    """英文分句：先把公式藏起来，再按句末标点切，跳过常见缩写。"""
    maths = []

    def hide(m):
        maths.append(m.group(0))
        return f"\x00{len(maths) - 1}\x00"

    t = _MATH.sub(hide, text)
    parts, start = [], 0
    for m in re.finditer(r"[.!?][\"')\]]*\s+(?=[A-Z(\[\"`*\x00])", t):
        end = m.end()
        chunk = t[start:end].strip()
        last = chunk.split()[-1] if chunk.split() else ""
        if last in ABBREV or re.fullmatch(r"[A-Z]\.", last) or last.endswith("et al."):
            continue
        parts.append(chunk)
        start = end
    tail = t[start:].strip()
    if tail:
        parts.append(tail)

    def unhide(s):
        return re.sub(r"\x00(\d+)\x00", lambda m: maths[int(m.group(1))], s)

    # 「1.」「(a)」「iii.」这类短标签不单独成句，并入下一句
    merged = []
    for p in parts:
        if merged and re.fullmatch(r"\(?[0-9A-Za-z]{1,4}[.)]", merged[-1]):
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return [unhide(p) for p in merged if p]


def _seg(segs, sid, kind, section, subsection, en, **extra):
    en = _clean(en)
    if not en and kind not in ("equation",):
        return
    d = {"id": sid, "kind": kind, "section": section, "subsection": subsection, "en": en,
         "sentences": split_sentences(en) if kind != "equation" else []}
    d.update(extra)
    segs.append(d)


def _flush_footnotes(segs, ctx, section, subsection, start_index):
    while start_index[0] < len(ctx.footnotes):
        n = start_index[0] + 1
        content = ctx.footnotes[start_index[0]]
        start_index[0] += 1
        if content is None:
            continue
        txt = _text_of(content, ctx, in_note=True)
        txt = re.sub(r"^\s*(footnote:\s*)?", "", txt)
        _seg(segs, f"fn{n}", "footnote", section, subsection, txt, footnote=n)


def extract(html, arxiv_id, base_url="https://arxiv.org/html/"):
    soup = BeautifulSoup(html, "lxml")
    for bad in soup.select("nav, .ltx_page_footer, .ltx_bibliography, .ltx_TOC, header, footer"):
        bad.decompose()
    ctx = Ctx()
    fn_done = [0]
    segs = []
    title_el = soup.find(class_="ltx_title_document")
    title = _clean(_text_of(title_el, ctx)) if title_el else arxiv_id

    ab = soup.find(class_="ltx_abstract")
    if ab:
        for i, p in enumerate(ab.find_all("p", class_="ltx_p")):
            _seg(segs, f"abs.p{i + 1}", "abstract", "Abstract", "", _text_of(p, ctx))
        _flush_footnotes(segs, ctx, "Abstract", "", fn_done)

    article = soup.find("article") or soup
    section, subsection = "", ""
    seen_fig = set()
    for el in article.find_all(["h2", "h3", "h4", "div", "figure", "table", "li"]):
        cls = el.get("class", [])
        if el.find_parent(class_="ltx_abstract") or el.find_parent(class_="ltx_note"):
            continue
        if el.name in ("h2",) and "ltx_title" in cls:
            section, subsection = _clean(_text_of(el, ctx)), ""
            if section.lower().startswith("acknowledg"):
                section = "__skip__"
            continue
        if section == "__skip__":
            continue
        if el.name in ("h3", "h4") and "ltx_title" in cls:
            subsection = _clean(_text_of(el, ctx))
            continue
        if el.name == "figure" and ("ltx_figure" in cls or "ltx_table" in cls):
            if el.get("id") in seen_fig or el.find_parent("figure"):
                continue
            seen_fig.add(el.get("id"))
            # 图注取最外层 figure 自己的 figcaption（子图的小标题不算）
            caps = [c for c in el.find_all("figcaption") if c.find_parent("figure") is el]
            cap = caps[-1] if caps else el.find("figcaption")
            images = []
            for g in el.find_all(["img", "object"]):  # arXiv HTML 里的图常用 <object data="...svg">
                src = g.get("src") if g.name == "img" else g.get("data")
                if not src:
                    continue
                url = src if src.startswith("http") else base_url + src.lstrip("/")
                if url not in images:
                    images.append(url)
            kind = "table" if "ltx_table" in cls else "caption"
            if cap:
                _seg(segs, el.get("id") or f"fig{len(seen_fig)}", kind, section, subsection,
                     _text_of(cap, ctx), image=images[0] if images else None, images=images)
            _flush_footnotes(segs, ctx, section, subsection, fn_done)
            continue
        if el.name == "div" and "ltx_para" in cls:
            if el.find_parent("figure") or el.find_parent("li", class_="ltx_item"):
                continue
            for child in el.children:
                if not isinstance(child, Tag):
                    continue
                ccls = child.get("class", [])
                if child.name == "p" and "ltx_p" in ccls:
                    _seg(segs, child.get("id") or el.get("id"), "para", section, subsection, _text_of(child, ctx))
                elif child.name == "table" and "ltx_equation" in ccls:
                    m = child.find("math")
                    alt = (m.get("alttext") or "") if m else ""
                    tag = child.find(class_="ltx_tag_equation")
                    _seg(segs, child.get("id") or "eq", "equation", section, subsection, "",
                         math=alt, eqtag=_clean(tag.get_text()) if tag else "")
            _flush_footnotes(segs, ctx, section, subsection, fn_done)
            continue
        if el.name == "li" and "ltx_item" in cls:
            tag = el.find(class_="ltx_tag_item")
            body = "".join(_text_of(p, ctx) + " " for p in el.find_all("p", class_="ltx_p"))
            lead = (_clean(tag.get_text()) + " ") if tag else ""
            _seg(segs, el.get("id") or "item", "item", section, subsection, lead + body)
            _flush_footnotes(segs, ctx, section, subsection, fn_done)
    return {"arxiv_id": arxiv_id, "title": title, "source": "arxiv-html",
            "base_url": base_url, "segments": segs}


if __name__ == "__main__":
    import sys
    html = open(sys.argv[1], encoding="utf-8").read()
    doc = extract(html, sys.argv[2])
    json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
