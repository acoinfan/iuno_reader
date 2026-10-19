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
_DOT = {"red": "#d9534f", "orange": "#e08a2c", "yellow": "#c9a200", "blue": "#4a86c5"}  # 句末小圆点
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
            # 用 Obsidian 的折叠框装图和图注，标题写「图 N」，正文字号不缩小
            m = re.match(r"\s*(Figure|Fig\.|Table|Tab\.)\s*(\d+)", seg["en"])
            label = (("表 " if m.group(1).lower().startswith("tab") else "图 ") + m.group(2)) if m else "图"
            box = [f"> [!example] {label}"]
            for url in seg.get("images") or ([seg["image"]] if seg.get("image") else []):
                box += [f"> ![]({url})", ">"]
            box += [f"> {en}", ">", f"> {zh}"]
            lines += box + [""]
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
      --ink:#141414;--sub:#5f5f5f;--rule:#e6e2d8;--hl:#e4f1e8;
      --zhw:500;--zhb:800;--enstroke:.35px}
html{background:#fdfcf8}
body{margin:0;color:var(--ink)}
/* 字体粗细三档：细 / 中（默认）/ 粗。英文 CMU Serif 只有常规和粗体两种，用细描边加粗；中文思源宋体是可变字重 */
body.w1{--zhw:400;--zhb:700;--enstroke:0px}
body.w3{--zhw:600;--zhb:900;--enstroke:.6px}
.en{-webkit-text-stroke:var(--enstroke) currentColor}
.zh{font-weight:var(--zhw)}
.zh b{font-weight:var(--zhb)}
/* 英文：左对齐让词距均匀（两端对齐会把空隙撑得忽宽忽窄，眼睛要不停找词的边界），再稍微加宽词距 */
body .en{text-align:left;word-spacing:.06em;hyphens:manual}
body.wide .en{word-spacing:.26em}
/* 突出实词：名词、专有名词、动词保持深色并加粗一点，其余词变浅灰 */
.w{cursor:pointer;border-radius:2px}
body.kw .en .w{color:#8d8d8d}
body.kw .en .w.k{color:var(--ink);-webkit-text-stroke:calc(var(--enstroke) + .4px) currentColor}
/* 点词高亮：最多三个词，三种底色 */
.f1{background:#c9e9cf!important}.f2{background:#cfdff7!important}.f3{background:#f1d6ee!important}
.p.f1 .w,.p.f2 .w,.p.f3 .w{background:transparent!important}
.nb{cursor:pointer;font-size:.8em;margin-left:2px;vertical-align:.1em;user-select:none}
.clear{display:none}
body.focusing .clear{display:inline}
.weights span{cursor:pointer;padding:0 6px;border-radius:3px}
.weights span.on{background:#e4f1e8;color:#2f7a4c}
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
body.plain .s.f{background:none!important}
body.plain .nb{display:none}
.notes{grid-column:1/3;display:none;margin:8px 0 2px;padding:10px 14px;background:#f5f2ea;border-radius:6px;
       font-family:var(--zh);font-size:13.5px;line-height:1.8;color:#444}
.para.open .notes{display:block}
.notes div{margin:3px 0}
/* 图块：不用小字，用浅暖灰底色整体区分；开头的「Figure N / 图 N」做成绿色标签 */
figure{margin:30px -26px 34px;padding:24px 26px 18px;background:#f4f1e9;border-radius:12px}
figure .imgs{display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-end;gap:12px;margin:0 auto 18px;max-width:88%}
figure img{display:block;max-height:460px;max-width:100%;flex:1 1 0;min-width:0;object-fit:contain;background:#fff;border-radius:4px}
figure .imgs img:only-child{max-width:82%;flex:0 1 auto}
figure .en{font-size:16.5px;line-height:1.68}
figure .zh{font-size:15.5px;line-height:1.9;text-align:left}
.figlabel{color:#2f7a4c;font-weight:bold;margin-right:.35em;-webkit-text-stroke:0}
.zh .figlabel{font-weight:800}
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
});
// 点有标记句子后面的小圆点，展开或收起这一段的说明
document.querySelectorAll('.nb').forEach(el=>el.addEventListener('click',e=>{
  e.stopPropagation();el.closest('.para').classList.toggle('open');}));
// 点词高亮：同一个键（词的原形，或术语表词组）全文一起铺底色；中文栏里对应的术语译名也一起亮
const focus=[];  // 当前高亮的键，最多三个
function paint(){
  document.querySelectorAll('.f1,.f2,.f3').forEach(x=>x.classList.remove('f1','f2','f3'));
  focus.forEach((k,i)=>document.querySelectorAll(`[data-l="${CSS.escape(k)}"]`).forEach(x=>x.classList.add('f'+(i+1))));
  document.body.classList.toggle('focusing',focus.length>0);}
document.addEventListener('click',e=>{
  const w=e.target.closest('.w,.g');if(!w) return;
  const p=w.closest('.p');const k=(p&&p.dataset.l)||w.dataset.l;if(!k) return;
  const i=focus.indexOf(k);
  if(i>=0) focus.splice(i,1); else {if(focus.length>=3) focus.shift(); focus.push(k);}
  paint();});
const C=document.getElementById('clear');
if(C) C.addEventListener('click',()=>{focus.length=0;paint();});
function sw(id,cls,on_txt,off_txt,key){const el=document.getElementById(id);if(!el) return;
  const set=on=>{document.body.classList.toggle(cls,on);el.textContent=on?on_txt:off_txt;
    try{localStorage.setItem(key,on?'1':'0')}catch(e){}};
  el.addEventListener('click',()=>set(!document.body.classList.contains(cls)));
  let v='0';try{v=localStorage.getItem(key)||'0'}catch(e){}set(v==='1');}
sw('kwt','kw','突出实词：开','突出实词：关','iuno-reader-kw');
sw('wst','wide','加宽词距：开','加宽词距：关','iuno-reader-wide');
const W=document.querySelectorAll('.weights span');
function setW(w){document.body.classList.remove('w1','w2','w3');document.body.classList.add(w);
  W.forEach(x=>x.classList.toggle('on',x.dataset.w===w));try{localStorage.setItem('iuno-reader-weight',w)}catch(e){}}
W.forEach(x=>x.addEventListener('click',()=>setW(x.dataset.w)));
let saved='w2';try{saved=localStorage.getItem('iuno-reader-weight')||'w2'}catch(e){}
setW(saved);
const t=document.getElementById('toggle');
if(t) t.addEventListener('click',()=>{document.body.classList.toggle('plain');
  t.textContent=document.body.classList.contains('plain')?'显示标记':'隐藏标记';});
</script>"""


_NOT_KW = {"et", "al"}  # 引用里的 et al. 不算实词


def _en_html(sentence, terms, ann=None):
    """英文句子 → HTML：每个字母词包一层 <span class='w' data-l=原形>，实词加 k；
    术语表里的词组再包一层 <span class='p' data-l=词组>。公式、代码、占位符原样保留。
    spaCy 不可用时退回普通转义。"""
    from . import nlp
    if not nlp.available():
        return _h(sentence)
    ann = ann if ann is not None else nlp.analyze(sentence)
    # 找术语表里的多词术语在本句的位置（按词原形连续匹配，中间只允许空格或连字符）
    starts, ends = {}, {}
    for t in sorted(terms, key=len, reverse=True):
        parts = re.split(r"[\s\-]+", t.strip().lower())
        if len(parts) < 2:
            continue
        key = nlp.term_key(t)
        for i in range(len(ann) - len(parts) + 1):
            if any(j in starts or j in ends for j in range(i, i + len(parts))):
                continue
            ok = all(ann[i + j][2] == parts[j] or sentence[ann[i + j][0]:ann[i + j][1]].lower() == parts[j]
                     for j in range(len(parts)))
            ok = ok and all(re.fullmatch(r"[\s\-]+", sentence[ann[i + j][1]:ann[i + j + 1][0]])
                            for j in range(len(parts) - 1))
            if ok:
                starts[i], ends[i + len(parts) - 1] = key, True
    out, last, bold = [], 0, False

    def raw(txt):
        nonlocal bold
        pieces = txt.split("**")
        res = []
        for n, piece in enumerate(pieces):
            if n:
                res.append("</b>" if bold else "<b>")
                bold = not bold
            piece = html.escape(piece, quote=False)
            piece = re.sub(r"`([^`]+)`", r"<code>\1</code>", piece)
            res.append(_fn(piece))
        return "".join(res)

    for i, (a, b, key, kw) in enumerate(ann):
        out.append(raw(sentence[last:a]))
        if i in starts:
            out.append(f"<span class='p' data-l='{_attr(starts[i])}'>")
        k = " k" if kw and key not in _NOT_KW else ""
        out.append(f"<span class='w{k}' data-l='{_attr(key)}'>{html.escape(sentence[a:b])}</span>")
        if i in ends:
            out.append("</span>")
        last = b
    out.append(raw(sentence[last:]))
    if bold:
        out.append("</b>")
    return "".join(out)


def _zh_html(sentence, mapping):
    """中文句子 → HTML，并把出现的术语译名包成 <span class='g' data-l=键>，点它和点英文词一样高亮。"""
    h = _h(sentence)
    if not mapping:
        return h
    pat = re.compile("|".join(re.escape(z) for z in sorted(mapping, key=len, reverse=True)))
    parts = re.split(r"(<[^>]+>)", h)
    for n, part in enumerate(parts):
        if part.startswith("<") or "$" in part:
            continue
        parts[n] = pat.sub(lambda m: f"<span class='g' data-l='{_attr(mapping[m.group(0)])}'>{m.group(0)}</span>", part)
    return "".join(parts)


def _term_mapping(en, terms_all, ann=None):
    """本句英文里出现的术语 → {中文译名: 高亮键}。
    单词术语的键取它在这一句里实际还原出的原形，保证和英文栏点词时用的键一致。"""
    from . import nlp
    from .glossary import terms_in
    if not terms_all or not nlp.available():
        return {}, []
    found = terms_in(re.sub(r"\$[^$]*\$", " ", en), terms_all)
    mapping = {}
    for t in found:
        key = nlp.term_key(t)
        if ann and not re.search(r"[\s\-]", t.strip()):
            tl = t.lower()
            for a, b, k, _ in ann:
                if en[a:b].lower() in (tl, tl + "s", tl + "es") or k == key:
                    key = k
                    break
        for z in terms_all[t]["zh"]:
            for v in {z, z.rstrip("的")}:
                if len(v) >= 2:
                    mapping.setdefault(v, key)
    return mapping, found


_LABEL_EN = re.compile(r"^\s*(Figure|Fig\.|Table|Tab\.)\s*(\d+)\s*[:.]\s*")
_LABEL_ZH = re.compile(r"^\s*(图|表)\s*(\d+)\s*[：:]\s*")


def _canon_table(segments):
    """整篇统一高亮键：统计每个词形（小写）最常被还原成的原形。
    spaCy 对同一个词在不同句子里可能给出不同原形（如 prefilled / prefille），统一后点一处全文都能亮。"""
    from collections import Counter, defaultdict
    from . import nlp
    if not nlp.available():
        return {}
    votes = defaultdict(Counter)
    for seg in segments:
        for p in seg.get("pairs") or []:
            for a, b, k, _ in nlp.analyze(p["en"]):
                votes[p["en"][a:b].lower()][k] += 1
    return {w: c.most_common(1)[0][0] for w, c in votes.items()}


def _pair_html(seg, terms_all=None, canon=None):
    en_parts, zh_parts, notes = [], [], []
    pairs = seg.get("pairs") or [{"en": x, "zh": "", "level": None, "flags": []} for x in seg["sentences"]]
    for i, p in enumerate(pairs, 1):
        sid = f"{seg['id']}-{i}"
        cls, style, title, nb = "s", "", "", ""
        if p["level"]:
            cls += " f"
            style = f" style='background:{COLOR[p['level']]}'"
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            title = f" title='{_attr(tip)}'"
            notes.append(f"<div><b>第 {i} 句</b>　{html.escape(tip)}</div>")
            nb = f"<span class='nb' title='点击展开说明' style='color:{_DOT[p['level']]}'>●</span>"
        en_raw, zh_raw, lab_en, lab_zh = p["en"], p["zh"] or "", "", ""
        if i == 1 and seg["kind"] in ("caption", "table"):
            m = _LABEL_EN.match(en_raw)
            if m:
                lab_en, en_raw = f"<span class='figlabel'>{m.group(1)} {m.group(2)}</span>", en_raw[m.end():]
            m = _LABEL_ZH.match(zh_raw)
            if m:
                lab_zh, zh_raw = f"<span class='figlabel'>{m.group(1)} {m.group(2)}</span>", zh_raw[m.end():]
        from . import nlp
        ann = nlp.analyze(en_raw) if nlp.available() else None
        if ann and canon:
            ann = [(a, b, canon.get(en_raw[a:b].lower(), k), kw) for a, b, k, kw in ann]
        mapping, found = _term_mapping(en_raw, terms_all, ann)
        en_parts.append(f"<span class='{cls}' data-s='{sid}'{style}>{lab_en}{_en_html(en_raw, found, ann)}</span> ")
        zh_parts.append(f"<span class='{cls}' data-s='{sid}'{style}{title}>{lab_zh}{_zh_html(zh_raw, mapping)}</span>{nb}"
                        if p["zh"] else "")
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
           f"共 {total} 句，有标记 {total - s.get('ok', 0)} 句　·　<span class='toggle' id='toggle'>隐藏标记</span>"
           "　·　<span class='weights'>字体粗细 <span data-w='w1'>细</span><span data-w='w2'>中</span><span data-w='w3'>粗</span></span>"
           "　·　<span class='toggle' id='kwt' title='名词、专有名词、动词保持深色并稍加粗，其余词变浅灰'>突出实词：关</span>"
           "　·　<span class='toggle' id='wst'>加宽词距：关</span>"
           "　·　<span class='toggle clear' id='clear'>清除高亮</span><br>"
           f"<span class='chips'>{chips}</span><br>{html.escape(_review_line(stats, review_info))}"
           "　点任意英文单词，全文同一个词（含中文栏里对应的术语）一起高亮，最多三个；"
           "有底色的句子悬停看原因，点句末小圆点展开说明；标记只是提醒，没标出的也不保证完全正确。</div></header>"]
    from .glossary import load_terms
    terms_all = load_terms()
    ordered = reading_order(doc["segments"], only)
    canon = _canon_table(ordered)
    cur_sec = cur_sub = None
    for seg in ordered:
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
        en, zh, notes = _pair_html(seg, terms_all, canon)
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
