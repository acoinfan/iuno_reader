"""逐句核对英文原文和中文译文，找出可能丢失或改变含义的地方。

等级（从重到轻）：
  red    规则发现可能丢了信息：否定、数字、公式、代码、占位符、引用、限定词丢失或被加强、漏译
  orange 翻译者自评低置信；译文明显过短（可能漏译）
  yellow 翻译者自评中等；译文明显过长（可能增译）；残留大段英文
  blue   术语与术语表不一致（提醒）
这些都是启发式规则：标出来的句子需要人看一眼，没标出来也不保证完全正确。
"""
import re
from collections import Counter

from .glossary import terms_in

LEVELS = ["red", "orange", "yellow", "blue"]
RANK = {lv: i for i, lv in enumerate(LEVELS)}

MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$")
CODE = re.compile(r"`[^`]+`")
PLACEHOLDER = re.compile(r"\{fn\d+\}")
YEAR = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b")
NUM = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?![\d])")

NEG_EN = re.compile(
    r"\b(?:not|no|never|cannot|without|neither|nor|none|nothing|nobody|unable|"
    r"fail(?:s|ed|ure|ures|ing)?|lack(?:s|ed|ing)?|absent|rather than|instead of|inert)\b|n't\b",
    re.I)
NEG_ZH = re.compile(r"不|没|無|无|未|非|否|别|勿|难以|缺|失败|而非|并非|欠|惰性")

NUM_WORDS = {
    "one": "一1", "two": "二两2", "three": "三3", "four": "四4", "five": "五5",
    "six": "六6", "seven": "七7", "eight": "八8", "nine": "九9", "ten": "十10",
    "twice": "两二2", "half": "半", "dozen": "十二12", "hundred": "百", "thousand": "千",
}


def _norm_math(s):
    return re.sub(r"\s+", "", s)


def _strip_spans(s):
    return CODE.sub(" ", MATH.sub(" ", s))


def check_sentence(en, zh, terms, hedges, conf=None, note=""):
    flags = []

    def add(level, code, msg):
        flags.append({"level": level, "code": code, "msg": msg})

    zh = (zh or "").strip()
    if not zh:
        add("red", "missing", "这一句没有译文")
        return flags

    # 1. 公式、代码、脚注占位符必须原样保留
    en_math = Counter(_norm_math(m) for m in MATH.findall(en))
    zh_math = Counter(_norm_math(m) for m in MATH.findall(zh))
    lost = en_math - zh_math
    if lost:
        add("red", "math", "公式没有原样保留：" + "、".join(list(lost)[:3]))
    for c in CODE.findall(en):
        if c not in zh and c.strip("`") not in zh:
            add("red", "code", f"代码或专名 {c} 没有原样保留")
    for p in PLACEHOLDER.findall(en):
        if p not in zh:
            add("red", "placeholder", f"脚注标记 {p} 丢了")

    en_plain = _strip_spans(en)
    zh_plain = _strip_spans(zh)

    # 2. 引用年份、数字
    for y in YEAR.findall(en_plain):
        if y not in zh:
            add("red", "citation", f"引用年份 {y} 没有出现在译文里")
    years = set(re.findall(r"\d{4}", " ".join(YEAR.findall(en_plain))))
    for n in NUM.findall(en_plain):
        if n in years:
            continue
        if n not in zh_plain and n not in zh:
            add("red", "number", f"数字 {n} 没有出现在译文里")
    # one 作代词时不是数量词：one another（彼此）、One might/may/could…（有人、人们）
    en_num = re.sub(r"\bone another\b|\b[Oo]ne (?:might|may|could|can|would|should|must)\b", " ", en_plain)
    for w, zh_opts in NUM_WORDS.items():
        if re.search(rf"\b{w}\b", en_num, re.I) and not any(ch in zh for ch in zh_opts):
            add("red", "number_word", f"原文的数量词 '{w}' 在译文里没找到对应")

    # 3. 否定
    en_neg = NEG_EN.findall(en_plain)
    if en_neg and not NEG_ZH.search(zh_plain):
        add("red", "negation", "原文有否定或否定性的词（" + "、".join(sorted(set(x.lower() for x in en_neg))) + "），译文里没找到否定表达")

    # 4. 限定词：丢失或被加强
    for h in hedges:
        m = h["en"].search(en_plain)
        if not m:
            continue
        ok = any(o in zh for o in h["zh_ok"])
        forbid = [f for f in h["zh_forbid"] if f in zh]
        if forbid and not ok:
            add("red", "hedge_strengthened", f"限定词 '{m.group(0)}' 可能被译重了（译文出现「{'、'.join(forbid)}」）")
        elif not ok:
            add("red", "hedge_missing", f"限定词 '{m.group(0)}' 在译文里没找到对应（可接受：{'/'.join(h['zh_ok'][:5])}）")
        elif forbid:
            add("orange", "hedge_mixed", f"限定词 '{m.group(0)}' 有对应，但译文同时出现「{'、'.join(forbid)}」，请确认语气")

    # 5. 术语表
    for t in terms_in(en_plain, terms):
        opts = terms[t]["zh"]
        hit = any(o in zh or o.rstrip("的") in zh for o in opts) or t.lower() in zh.lower()
        if not hit:
            add("blue", "term", f"术语 '{t}' 没按术语表译（术语表：{'/'.join(opts)}）")

    # 6. 英文残留：去掉引用、术语和专名后，仍有连续 4 个以上英文单词
    residue = re.sub(r"[A-Z][A-Za-z\-]+(?: et al\.)?,?\s*\(?(?:19|20)\d{2}[a-z]?\)?", " ", zh_plain)
    for t in terms:
        residue = re.sub(re.escape(t), " ", residue, flags=re.I)
    runs = re.findall(r"(?:[A-Za-z][A-Za-z\-']*\s+){3,}[A-Za-z][A-Za-z\-']*", residue)
    if runs:
        add("yellow", "residue", f"译文里残留较长的英文：「{runs[0][:40]}」")

    # 7. 长度比例（中文字符数 / 英文单词数）
    en_words = len(re.findall(r"[A-Za-z]+", en_plain))
    zh_chars = len(re.findall(r"[一-鿿]", zh_plain))
    if en_words >= 8:
        ratio = zh_chars / en_words
        if ratio < 0.8:
            add("orange", "too_short", f"译文偏短（每个英文词约 {ratio:.2f} 个汉字），可能漏译")
        elif ratio > 3.6:
            add("yellow", "too_long", f"译文偏长（每个英文词约 {ratio:.2f} 个汉字），可能有增译")

    # 8. 翻译者自评
    if conf == "low":
        add("orange", "self_low", "翻译者自评低置信" + (f"：{note}" if note else ""))
    elif conf == "mid":
        add("yellow", "self_mid", "翻译者自评中等置信" + (f"：{note}" if note else ""))
    return flags


def top_level(flags):
    if not flags:
        return None
    return min((f["level"] for f in flags), key=lambda lv: RANK[lv])


def check_doc(doc, translation, terms, hedges, reviews=None):
    """给 doc 的每个片段挂上 zh 和 flags，返回 (doc, 统计)。

    reviews：若干份审校结果（review.json 的内容）组成的列表，意见会并到对应句子上。"""
    from .review import review_flags
    reviews = reviews or []
    stats = Counter()
    for seg in doc["segments"]:
        tr = translation.get(seg["id"])
        seg["translated"] = tr is not None
        if not tr or seg["kind"] == "equation":
            continue
        seg["zh_title"] = tr.get("zh_title")
        sents = tr.get("sentences", [])
        seg["seg_flags"] = []
        if len(sents) != len(seg["sentences"]):
            seg["seg_flags"].append({"level": "red", "code": "count",
                                     "msg": f"句数对不上：原文 {len(seg['sentences'])} 句，译文 {len(sents)} 句"})
        out = []
        for i, en in enumerate(seg["sentences"]):
            s = sents[i] if i < len(sents) else {}
            flags = check_sentence(en, s.get("zh", ""), terms, hedges, s.get("conf"), s.get("note", ""))
            for rv in reviews:
                seg_rv = rv.get(seg["id"])
                if not seg_rv:
                    continue
                items = seg_rv.get("reviews", [])
                if len(items) != len(seg["sentences"]):
                    continue  # 条数对不上的审校结果不采用，避免错位
                item = items[i]
                if "zh_reviewed" in item and item["zh_reviewed"].strip() != (s.get("zh") or "").strip():
                    stats["reviewed_stale"] += 1  # 译文改过了，旧审校意见作废
                    continue
                stats["reviewed_" + seg_rv.get("mode", "?")] += 1
                for f in review_flags(item):
                    f["msg"] = f"[{seg_rv.get('reviewer', '?')}] " + f["msg"]
                    flags.append(f)
            lv = top_level(flags)
            stats[lv or "ok"] += 1
            out.append({"en": en, "zh": s.get("zh", ""), "conf": s.get("conf"), "note": s.get("note", ""),
                        "flags": flags, "level": lv})
        seg["pairs"] = out
    return doc, stats
