"""把核对后的 doc 渲染成 Obsidian Markdown 和左右两栏 HTML。

阅读体验上的约定：
- 图、表、脚注跟着正文走：放到正文第一次提到它的那一段后面（找不到引用的保持原位）。
- 不用卡片和边框，段落之间只用留白分隔；片段编号不显示。
- 有问题的句子用浅色底色标出；网页里鼠标悬停时左右对应句子一起铺浅绿底色，点击有底色的句子展开说明。
- 标题：网页里英文、中文左右两栏；Obsidian 里标题用中文，英文原标题小字放在下面。
- 字体：英文用 Computer Modern（CMU Serif，论文常用），中文用思源宋体 / 宋体。
"""
import datetime
import html
import re

from .check import LEVELS

COLOR = {"red": "#f7d3d3", "orange": "#f8dcbf", "yellow": "#f8f0c4", "blue": "#d9e8f7"}
ICON = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "blue": "🔵"}
LABEL = {"red": "可能丢了信息", "orange": "翻译没把握或可能漏译", "yellow": "需要留意", "blue": "术语与术语表不一致"}

def _fn(s):
    return re.sub(r"\{fn(\d+)\}", r"<sup>\1</sup>", s)


def _attr(s):
    return html.escape(s, quote=True).replace("\n", " ")


def reading_order(segments, only=None):
    """返回按阅读顺序排列的片段：图、表、脚注挪到正文第一次提到它的段落后面。"""
    from .extract import FLOAT_KINDS, float_ref_pattern
    segs = [s for s in segments if only is None or s["id"] in only]
    main = [s for s in segs if s["kind"] not in FLOAT_KINDS]
    anchor = {}
    for f in segs:
        if f["kind"] not in FLOAT_KINDS:
            continue
        pat = float_ref_pattern(f)
        if not pat:
            continue
        for s in main:
            if pat.search(s["en"]):
                anchor[f["id"]] = s["id"]
                break
    attached = {}
    for f in segs:
        if f["id"] in anchor:
            attached.setdefault(anchor[f["id"]], []).append(f)
    out = []
    for s in segs:
        if s["id"] in anchor:
            continue  # 会在它的锚点段落后面输出
        out.append(s)
        for f in attached.get(s["id"], []):
            out.append(dict(f, section=s["section"], subsection=s.get("subsection", "")))
    return out


def _review_line(stats, review_info=None):
    from .roles import INDEPENDENCE_ZH
    names = {"blind": "盲审", "backtranslate": "回译比对"}
    parts = [f"{names.get(k[9:], k[9:])} {v} 句" for k, v in stats.items()
             if k.startswith("reviewed_") and k != "reviewed_stale"]
    if not parts:
        return "还没有经过独立审校：没有标记的句子只表示规则没发现问题。"
    line = "已审校：" + "，".join(parts)
    if stats.get("reviewed_stale"):
        line += f"；另有 {stats['reviewed_stale']} 句在审校后改过译文，旧意见已作废"
    who = []
    for m in review_info or []:
        if not m:
            continue
        ind = INDEPENDENCE_ZH.get(m.get("independence", "unknown"), "独立性未知")
        who.append(f"{m.get('reviewer', '?')}（{names.get(m.get('mode'), m.get('mode'))}，{ind}）")
    if who:
        line += "。审校者：" + "；".join(who)
    return line + "。"


def _counts(stats):
    s = {k: v for k, v in stats.items() if not k.startswith("reviewed_")}
    return s, sum(s.values())


# ---------------------------------------------------------------- Markdown（Obsidian）

def _zh_md(pairs):
    out = []
    for p in pairs:
        zh = _fn(p["zh"] or "（缺译文）")
        if p["level"]:
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            zh = f'<mark style="background:{COLOR[p["level"]]};color:inherit" title="{_attr(tip)}">{zh}</mark>'
        out.append(zh)
    return "".join(out)


def _flags_md(seg):
    items = []
    for f in seg.get("seg_flags", []):
        items.append(f"> - {ICON[f['level']]} 整段：{f['msg']}")
    for i, p in enumerate(seg.get("pairs", []), 1):
        if not p["level"]:
            continue
        reasons = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
        items.append(f"> - 第 {i} 句：{reasons}\n>     - 原文：{_fn(p['en'])}\n>     - 译文：{_fn(p['zh'])}")
    if not items:
        return ""
    return f"> [!note]- {len(items)} 处可以核对\n" + "\n".join(items) + "\n"


def render_markdown(doc, stats, sections_zh=None, only=None, review_info=None):
    sections_zh = sections_zh or {}
    s, total = _counts(stats)
    title_zh = sections_zh.get("__title__")
    chips = " ".join(f'<mark style="background:{COLOR[lv]};color:inherit">{LABEL[lv]} {s.get(lv, 0)}</mark>' for lv in LEVELS)
    lines = [
        "---", f"arxiv: \"{doc['arxiv_id']}\"", f"title: \"{doc['title']}\"",
        f"generated: {datetime.date.today().isoformat()}", "tool: iuno_reader", "cssclasses: [iuno-reader]", "---", "",
        f"# {title_zh or doc['title']}", "",
    ]
    if title_zh:
        lines += [f"<small>{doc['title']}</small>", ""]
    lines += [f"<small>arXiv {doc['arxiv_id']} ｜ https://arxiv.org/abs/{doc['arxiv_id']}</small>", "",
              f"> [!info]- 关于这份译文（共 {total} 句，有标记 {total - s.get('ok', 0)} 句）",
              "> 每段先是英文原文，接着是中文译文。有底色的句子值得核对：鼠标悬停看原因，段落后的折叠框里有逐句对照。",
              f"> {chips}",
              f"> {_review_line(stats, review_info)}",
              "> 标记只是提醒：标出的句子要看一眼，没标出的也不保证完全正确。", ""]
    cur_sec = cur_sub = None
    for seg in reading_order(doc["segments"], only):
        if seg["section"] != cur_sec:
            cur_sec, cur_sub = seg["section"], None
            zh = sections_zh.get(cur_sec)
            lines += [f"## {zh or cur_sec}", ""] + ([f"<small>{cur_sec}</small>", ""] if zh else [])
        if seg.get("subsection") and seg["subsection"] != cur_sub:
            cur_sub = seg["subsection"]
            zh = sections_zh.get(cur_sub)
            lines += [f"### {zh or cur_sub}" + (f" <small>{cur_sub}</small>" if zh else ""), ""]
        kind = seg["kind"]
        if kind == "equation":
            lines += [f"$${seg.get('math', '')}$$", ""]
            continue
        en = _fn(seg["en"])
        zh = _zh_md(seg["pairs"]) if seg.get("pairs") else "*（这一段还没有翻译）*"
        if kind == "footnote":
            n = seg.get("footnote")
            lines += [f"<small>注 {n}：{en}</small>", "", f"<small>注 {n}：{zh}</small>", ""]
        elif kind in ("caption", "table"):
            for url in seg.get("images") or ([seg["image"]] if seg.get("image") else []):
                lines += [f"![]({url})", ""]
            lines += [f"<small>{en}</small>", "", f"<small>{zh}</small>", ""]
        else:
            lines += [en, "", zh, ""]
        fl = _flags_md(seg)
        if fl:
            lines += [fl]
    return "\n".join(lines)


# ---------------------------------------------------------------- HTML（左右两栏）

def _h(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return _fn(s)


CSS = """
@import url('https://cdn.jsdelivr.net/npm/computer-modern@0.1.2/cmu-serif.min.css');
:root{--en:"CMU Serif","Latin Modern Roman","Computer Modern Serif","Times New Roman",Times,serif;
      --zh:"Noto Serif SC","Source Han Serif SC","Songti SC","SimSun","STSong",serif;
      --ink:#222;--sub:#6b6b6b;--rule:#e6e2d8;--hl:#e4f1e8}
html{background:#fdfcf8}
body{margin:0;color:var(--ink);-webkit-font-smoothing:antialiased}
main{max-width:1320px;margin:0 auto;padding:48px 56px 120px}
.pair{display:grid;grid-template-columns:1fr 1fr;column-gap:56px}
.en{font-family:var(--en);font-size:17.5px;line-height:1.72;hyphens:auto;text-align:justify}
.zh{font-family:var(--zh);font-size:16.5px;line-height:1.95;text-align:justify}
header{margin-bottom:40px;border-bottom:1px solid var(--rule);padding-bottom:28px}
header .title .en{font-size:28px;line-height:1.3;font-weight:normal;text-align:left}
header .title .zh{font-size:25px;line-height:1.45;font-weight:600;text-align:left}
.meta{font-size:13px;color:var(--sub);font-family:var(--zh);margin-top:14px;line-height:1.8}
.meta a{color:var(--sub)}
.chips span{display:inline-block;padding:1px 8px;border-radius:3px;margin-right:8px}
.toggle{cursor:pointer;user-select:none;color:#3b915d;text-decoration:underline;text-underline-offset:3px}
h2.pair{margin:56px 0 18px}
h2 .en{font-size:22px;font-weight:bold;text-align:left;line-height:1.35}
h2 .zh{font-size:21px;font-weight:700;text-align:left;line-height:1.5}
h3.pair{margin:30px 0 10px}
h3 .en{font-size:18px;font-weight:bold;font-style:italic;text-align:left}
h3 .zh{font-size:17.5px;font-weight:700;text-align:left}
.para{margin:0 0 1.15em}
.para p{margin:0}
.s{border-radius:2px;transition:background .12s}
.s.hl{background:var(--hl)!important}
.s.f{cursor:pointer}
body.plain .s.f{background:none!important;cursor:auto}
.notes{grid-column:1/3;display:none;margin:8px 0 2px;padding:10px 14px;background:#f5f2ea;border-radius:6px;
       font-family:var(--zh);font-size:13.5px;line-height:1.8;color:#444}
.para.open .notes{display:block}
.notes div{margin:3px 0}
figure{margin:28px 0 30px}
figure .imgs{display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-end;gap:12px;margin:0 auto 14px;max-width:86%}
figure img{display:block;max-height:460px;max-width:100%;flex:1 1 0;min-width:0;object-fit:contain;background:#fff}
figure .imgs img:only-child{max-width:80%;flex:0 1 auto}
figure .en,figure .zh{font-size:14.5px;line-height:1.65;color:#333}
figure .zh{font-size:14px}
.fn .en,.fn .zh{font-size:13.5px;color:var(--sub);line-height:1.6}
.fn{margin:-.4em 0 1.1em}
.eq{text-align:center;margin:10px 0 18px;overflow-x:auto;font-size:17px}
code{font-family:"CMU Typewriter Text",Consolas,monospace;font-size:.92em}
sup{font-size:.7em}
@media (max-width:860px){main{padding:28px 18px}.pair{grid-template-columns:1fr;row-gap:6px}}
"""

JS = """<script>
document.querySelectorAll('.s').forEach(el=>{
  const peers=()=>document.querySelectorAll(`.s[data-s="${el.dataset.s}"]`);
  el.addEventListener('mouseenter',()=>peers().forEach(x=>x.classList.add('hl')));
  el.addEventListener('mouseleave',()=>peers().forEach(x=>x.classList.remove('hl')));
  if(el.classList.contains('f')) el.addEventListener('click',()=>{
    if(document.body.classList.contains('plain')) return;
    el.closest('.para').classList.toggle('open');});
});
const t=document.getElementById('toggle');
if(t) t.addEventListener('click',()=>{document.body.classList.toggle('plain');
  t.textContent=document.body.classList.contains('plain')?'显示标记':'隐藏标记';});
</script>"""


def _pair_html(seg):
    en_parts, zh_parts, notes = [], [], []
    pairs = seg.get("pairs") or [{"en": x, "zh": "", "level": None, "flags": []} for x in seg["sentences"]]
    for i, p in enumerate(pairs, 1):
        sid = f"{seg['id']}-{i}"
        cls, style, title = "s", "", ""
        if p["level"]:
            cls += " f"
            style = f" style='background:{COLOR[p['level']]}'"
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            title = f" title='{_attr(tip)}'"
            notes.append(f"<div><b>第 {i} 句</b>　{html.escape(tip)}</div>")
        en_parts.append(f"<span class='{cls}' data-s='{sid}'{style}>{_h(p['en'])}</span> ")
        zh_parts.append(f"<span class='{cls}' data-s='{sid}'{style}{title}>{_h(p['zh'])}</span>" if p["zh"] else "")
    for f in seg.get("seg_flags", []):
        notes.insert(0, f"<div><b>整段</b>　{ICON[f['level']]} {html.escape(f['msg'])}</div>")
    zh = "".join(zh_parts) if seg.get("pairs") else "<span style='color:#999'>（这一段还没有翻译）</span>"
    return "".join(en_parts), zh, notes


def render_html(doc, stats, sections_zh=None, only=None, review_info=None):
    sections_zh = sections_zh or {}
    s, total = _counts(stats)
    title_zh = sections_zh.get("__title__", "")
    chips = "".join(f"<span style='background:{COLOR[lv]}'>{LABEL[lv]} {s.get(lv, 0)}</span>" for lv in LEVELS)
    out = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{html.escape(title_zh or doc['title'])}</title><style>{CSS}</style>",
           "<script>window.MathJax={tex:{inlineMath:[['$','$']],displayMath:[['$$','$$']]}};</script>",
           "<script src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js' async></script>",
           "</head><body><main><header>",
           f"<div class='pair title'><div class='en'>{html.escape(doc['title'])}</div><div class='zh'>{html.escape(title_zh)}</div></div>",
           f"<div class='meta'><a href='https://arxiv.org/abs/{doc['arxiv_id']}'>arXiv {doc['arxiv_id']}</a>　·　"
           f"共 {total} 句，有标记 {total - s.get('ok', 0)} 句　·　<span class='toggle' id='toggle'>隐藏标记</span><br>"
           f"<span class='chips'>{chips}</span><br>{html.escape(_review_line(stats, review_info))}"
           "　有底色的句子悬停看原因、点击展开说明；标记只是提醒，没标出的也不保证完全正确。</div></header>"]
    cur_sec = cur_sub = None
    for seg in reading_order(doc["segments"], only):
        if seg["section"] != cur_sec:
            cur_sec, cur_sub = seg["section"], None
            out.append(f"<h2 class='pair'><div class='en'>{html.escape(cur_sec)}</div>"
                       f"<div class='zh'>{html.escape(sections_zh.get(cur_sec, ''))}</div></h2>")
        if seg.get("subsection") and seg["subsection"] != cur_sub:
            cur_sub = seg["subsection"]
            out.append(f"<h3 class='pair'><div class='en'>{html.escape(cur_sub)}</div>"
                       f"<div class='zh'>{html.escape(sections_zh.get(cur_sub, ''))}</div></h3>")
        kind = seg["kind"]
        if kind == "equation":
            out.append(f"<div class='eq'>$${html.escape(seg.get('math', ''))}$$</div>")
            continue
        en, zh, notes = _pair_html(seg)
        note_html = f"<div class='notes'>{''.join(notes)}</div>" if notes else ""
        if kind in ("caption", "table"):
            urls = seg.get("images") or ([seg["image"]] if seg.get("image") else [])
            img = ("<div class='imgs'>" + "".join(f"<img src='{u}' alt=''>" for u in urls) + "</div>") if urls else ""
            out.append(f"<figure class='para'>{img}<div class='pair'><div class='en'>{en}</div>"
                       f"<div class='zh'>{zh}</div>{note_html}</div></figure>")
        elif kind == "footnote":
            n = seg.get("footnote")
            out.append(f"<div class='para fn pair'><div class='en'><sup>{n}</sup> {en}</div>"
                       f"<div class='zh'><sup>{n}</sup> {zh}</div>{note_html}</div>")
        else:
            out.append(f"<div class='para pair'><div class='en'><p>{en}</p></div>"
                       f"<div class='zh'><p>{zh}</p></div>{note_html}</div>")
    out.append(JS + "</main></body></html>")
    return "\n".join(out)
