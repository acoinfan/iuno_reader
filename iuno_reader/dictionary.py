"""本地英汉词典（ECDICT，MIT 协议，https://github.com/skywind3000/ECDICT），给双语网页的「查词」用。

词典文件放在 <根目录>/dict/ecdict.csv（不进 git）。第一次用时转成 dict/ecdict.db（SQLite，按词建索引），
之后渲染网页时把这篇论文里出现的词一次查好，嵌进网页，离线打开也能查。
没有词典文件时一切照旧，网页里不显示「查词」。
"""
import csv
import os
import re
import sqlite3

TAGS = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研", "toefl": "托福", "ielts": "雅思",
        "gre": "GRE"}
_COLS = ("word", "phonetic", "definition", "translation", "collins", "oxford", "tag", "frq", "exchange")


def paths(root):
    d = os.path.join(root, "dict")
    return os.path.join(d, "ecdict.csv"), os.path.join(d, "ecdict.db")


def build(csv_path, db_path):
    """ECDICT 的 CSV → SQLite。先写临时文件，写完再换上，中途失败不会留下半个库。"""
    csv.field_size_limit(10 ** 7)
    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute(f"create table d ({', '.join(c + ' text' for c in _COLS)})")
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = (tuple(r.get(c, "") for c in _COLS) for r in csv.DictReader(f))
        db.executemany(f"insert into d values ({','.join('?' * len(_COLS))})", rows)
    db.execute("create index i on d (word collate nocase)")
    db.commit()
    db.close()
    os.replace(tmp, db_path)


def open_db(root):
    """→ sqlite 连接；没有词典时返回 None。只有 CSV 时先建库。"""
    csv_path, db_path = paths(root)
    if not os.path.exists(db_path):
        if not os.path.exists(csv_path):
            return None
        build(csv_path, db_path)
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _brief(row):
    w, ph, de, tr, co, ox, tag, frq, ex = row
    lines = [x.strip() for x in (tr or "").split("\\n") if x.strip() and not x.startswith("[网络]")]
    out = {"w": w, "p": ph or "", "t": "\n".join(lines[:4])[:160]}
    d = [x.strip() for x in (de or "").split("\\n") if x.strip()]
    if d:
        out["d"] = re.sub(r"^(?:[nvasr]\s+|[a-z]{1,4}\.\s*)", "", d[0])[:120]  # 去掉开头的词性记号
    tags = [TAGS[t] for t in (tag or "").split() if t in TAGS]
    if co and co != "0":
        tags.append("柯林斯" + "★" * int(co))
    if ox == "1":
        tags.append("牛津3000")
    if tags:
        out["g"] = " ".join(tags)
    lem = dict(p.split(":", 1) for p in (ex or "").split("/") if ":" in p).get("0")
    if lem and lem.lower() != w.lower():
        out["l"] = lem
    return out


def lookup(db, words):
    """words：一组小写词形 → {词: 简要词条}。查不到原形时按 ECDICT 的变形表找原形词条。"""
    out = {}
    words = sorted({w for w in words if re.fullmatch(r"[a-z][a-z'\-]{1,40}", w)})
    q = f"select {', '.join(_COLS)} from d where word = ? collate nocase"
    for w in words:
        rows = db.execute(q, (w,)).fetchall()
        if not rows:
            continue
        row = next((r for r in rows if r[0] == w), rows[0])  # 优先小写原词，免得拿到专有名词
        e = _brief(row)
        if e.get("l"):  # 变形词：英文释义和考试标签用原形的；自己没有中文释义时也用原形的
            base = db.execute(q, (e["l"],)).fetchone()
            if base:
                b = _brief(base)
                e = (b if not e["t"] else e | {k: b[k] for k in ("d", "g") if k in b}) | {"w": e["w"], "l": e["l"]}
                if not b.get("d"):
                    e.pop("d", None)
                if (b["t"] or b.get("d")) and e["l"].lower() not in out:
                    out[e["l"].lower()] = {k: v for k, v in b.items() if k != "l"}  # 原形词条也带上，收生词时记原形
        if e["t"] or e.get("d"):
            out[w] = e
    return out
