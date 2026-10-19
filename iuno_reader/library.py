"""论文库：把参考文献对应到 arXiv 编号，以及你自己的知识库（收录的 PDF、译文、论文卡片）。

知识库目录由环境变量 IUNO_LIBRARY 或前端「设置」里的「知识库目录」指定。按 research-KB 的结构来读：
  pdfs/**/<arXiv 编号>_<名字>.pdf          收录的论文
  pdfs/<项目>/按优先级/<档>/<序号>_...pdf    阅读顺序（例如「cvpr4 · 精读-反面 #1」）
  pdfs/<项目>/<1-精读|2-细读|3-略读>/...pdf  阅读档次
  pdfs/**/INDEX.md                         清单，表格或粗体里有编号和标题
  translations/<编号>-zh.html / -zh.md     译文
  papers/*.md                              论文卡片（frontmatter 里有 title 和 url）

参考文献按顺序对应：条目里自带的 arXiv 编号 → 标题和知识库里的标题对上 → 拿标题去 arXiv 搜（结果缓存）。
"""
import difflib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_ID = r"\d{4}\.\d{4,5}"
_TIER = re.compile(r"^\d+-(精读|细读|略读)")


def norm(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def pid_from_name(name):
    """PDF 文件名 → 流水线编号：有 arXiv 编号就用它；否则去掉阅读顺序前缀（「06_」）和扩展名，只留字母、数字、点、横线。"""
    stem = os.path.splitext(os.path.basename(name.split("?")[0]))[0]
    m = re.search(_ID, stem)
    if m:
        return m.group(0)
    stem = re.sub(r"^\d{1,3}_", "", stem)
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")[:100]


def library_root():
    """知识库目录：环境变量优先，其次是前端设置；目录不存在时返回 None（不做库内对应）。"""
    d = os.environ.get("IUNO_LIBRARY", "").strip()
    if not d:
        from . import providers as pv
        d = ((pv._load("settings.json", {}) or {}).get("library_dir") or "").strip()
    return d if d and os.path.isdir(d) else None


def _label(rel):
    """pdfs/ 下的相对路径 → 「cvpr4 · 精读-反面 #1」这样的位置说明；返回 (说明, 是否阅读顺序)。"""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) < 3:
        return "", False
    proj = parts[1]
    if "按优先级" in parts:
        i = parts.index("按优先级")
        if i + 2 < len(parts):
            tier = re.sub(r"^\d+-", "", parts[i + 1])
            m = re.match(r"(\d+)_", parts[i + 2])
            return f"{proj} · {tier}" + (f" #{int(m.group(1))}" if m else ""), True
    if _TIER.match(parts[2]):
        return proj + " · " + re.sub(r"^\d+-", "", parts[2]), True
    return (f"{proj} · {parts[2]}" if len(parts) > 3 else proj), False


def _frontmatter(path):
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(3000)
    except OSError:
        return {}
    m = re.match(r"---\n(.*?)\n---", head, re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_library(root):
    """扫描知识库 → {"root", "vault", "papers": {键: 条目}}。键是 arXiv 编号；没有编号的 PDF 用流水线编号
    （文件名去掉序号前缀，见 pid_from_name），和它的译文 translations/<编号>-zh.html 对得上。
    条目：{id（arXiv 编号，非 arXiv 为空）, title, where: [位置说明], pdf, translation, card}"""
    papers, titles_by_file = {}, {}

    def entry(key):
        return papers.setdefault(key, {"id": key if re.fullmatch(_ID, key) else "", "title": "", "where": [],
                                       "pdf": "", "translation": "", "card": ""})

    pdf_root = os.path.join(root, "pdfs")
    for dirpath, _, files in os.walk(pdf_root):
        for fn in sorted(files):
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            if fn.endswith((".pdf", ".html")) and not fn.endswith("-zh.html"):
                m = re.search(rf"(?:^|_)({_ID})(?=_|\.pdf|v\d)", fn)
                key = m.group(1) if m else pid_from_name(fn)
                e = entry(key)
                lab, ordered = _label(os.path.relpath(os.path.join(dirpath, fn), root))
                if lab and lab not in e["where"]:
                    e["where"].insert(0 if ordered else len(e["where"]), lab)
                if not e["pdf"] or not ordered:  # PDF 链接优先用分类文件夹里的原件
                    e["pdf"] = rel
            elif fn == "INDEX.md":
                _titles_from_index(os.path.join(dirpath, fn), papers, titles_by_file, entry)
    for key, t in titles_by_file.items():
        if key in papers and not papers[key]["title"]:
            papers[key]["title"] = t
    tdir = os.path.join(root, "translations")
    for fn in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)-zh\.html", fn)
        if m:
            e = entry(m.group(1))
            e["translation"] = "translations/" + fn
            fm = _frontmatter(os.path.join(tdir, fn[:-5] + ".md"))
            e["title"] = e["title"] or fm.get("title", "")
    cdir = os.path.join(root, "papers")
    for fn in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
        if not fn.endswith(".md"):
            continue
        fm = _frontmatter(os.path.join(cdir, fn))
        m = re.search(_ID, fm.get("url", ""))
        if m:
            e = entry(m.group(0))
            e["card"] = "papers/" + fn
            e["title"] = fm.get("title") or e["title"]
    return {"root": root, "vault": os.path.basename(os.path.normpath(root)), "papers": papers}


def _titles_from_index(path, papers, titles_by_file, entry):
    """清单里的标题：表格行「| [编号](arxiv 链接) | 标题 | ...」或「**标题**（作者，编号，...）」。"""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            m = re.search(rf"\[({_ID})\]", cells[0]) if cells else None
            pdf = next((re.search(r"([^/`\s]+\.pdf)", c).group(1) for c in cells if re.search(r"\.pdf", c)), "")
            if m and len(cells) > 1:  # 「| [编号](链接) | 标题 | 作者 | ...」：编号后面那一格就是标题
                title = cells[1]
            else:
                title = next((c for c in cells[1:] if len(c.split()) >= 3 and ".pdf" not in c), "")
            title = re.sub(r"\*\*|`", "", title).strip()
            if m and title:
                titles_by_file.setdefault(m.group(1), title)
            elif pdf and title and not re.search(_ID, pdf):
                titles_by_file.setdefault(pid_from_name(pdf), title)
        for m in re.finditer(rf"\*\*([^*]{{8,}}?)\*\*（[^）]*?({_ID})", line):
            titles_by_file.setdefault(m.group(2), m.group(1).strip())


class Resolver:
    """参考文献 → {arxiv, lib}。arXiv 标题搜索和编号查标题的结果都缓存在 cache_path 里。"""

    def __init__(self, lib=None, cache_path=None, lookup=None):
        self.lib = lib
        self.cache_path = cache_path
        self.lookup = (os.environ.get("IUNO_ARXIV_LOOKUP", "1") != "0") if lookup is None else lookup
        self.cache = {"title": {}, "id": {}}
        if cache_path and os.path.exists(cache_path):
            try:
                self.cache.update(json.load(open(cache_path, encoding="utf-8")))
            except ValueError:
                pass
        self._last = 0.0
        self.by_title = {}
        if lib:
            self._fill_titles()
            for k, e in lib["papers"].items():
                if e["title"]:
                    self.by_title.setdefault(norm(e["title"]), k)

    def _lock(self):
        """几篇论文同时跑时共用一把文件锁：arXiv 请求排队，缓存写入互不覆盖。没有缓存文件时不加锁。"""
        import contextlib
        if not self.cache_path:
            return contextlib.nullcontext(None)
        import fcntl

        @contextlib.contextmanager
        def held():
            with open(self.cache_path + ".lock", "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    yield f
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        return held()

    def _save(self):
        if not self.cache_path:
            return
        with self._lock():
            try:  # 先并入别的进程写进去的结果
                old = json.load(open(self.cache_path, encoding="utf-8"))
                for k in ("title", "id"):
                    self.cache[k] = {**old.get(k, {}), **self.cache[k]}
            except (OSError, ValueError):
                pass
            tmp = f"{self.cache_path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)

    def _get(self, url):
        with self._lock() as f:
            last = self._last
            if f is not None:  # 上一次请求的时间记在锁文件里，所有进程共用
                f.seek(0)
                try:
                    last = max(last, float(f.read().strip() or 0))
                except ValueError:
                    pass
            wait = 3.1 - (time.time() - last)  # arXiv 要求两次请求间隔 3 秒
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            if f is not None:
                f.seek(0)
                f.truncate()
                f.write(str(self._last))
                f.flush()
        req = urllib.request.Request(url, headers={"User-Agent": "iuno_reader"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return ET.fromstring(r.read())

    @staticmethod
    def _entries(feed):
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in feed.findall("a:entry", ns):
            m = re.search(rf"({_ID})", e.findtext("a:id", "", ns))
            if m:
                yield m.group(1), re.sub(r"\s+", " ", e.findtext("a:title", "", ns)).strip()

    def _fill_titles(self):
        """库里只有编号、没有标题的论文，按编号批量去 arXiv 取标题。"""
        need = [k for k, e in self.lib["papers"].items() if e["id"] and not e["title"]]
        for k in need:
            if self.cache["id"].get(k):
                self.lib["papers"][k]["title"] = self.cache["id"][k]
        need = [k for k in need if not self.lib["papers"][k]["title"]]
        if not need or not self.lookup:
            return
        for i in range(0, len(need), 100):
            ids = need[i:i + 100]
            try:
                feed = self._get("https://export.arxiv.org/api/query?max_results=100&id_list=" + ",".join(ids))
            except (OSError, ET.ParseError):
                return
            for pid, title in self._entries(feed):
                self.cache["id"][pid] = title
                if pid in self.lib["papers"]:
                    self.lib["papers"][pid]["title"] = title
        self._save()

    def search_arxiv(self, title):
        """按标题在 arXiv 上找编号；标题要几乎完全一致才算。找不到返回空串（也缓存）。"""
        key = norm(title)
        if len(key) < 15:
            return ""
        if key in self.cache["title"]:
            return self.cache["title"][key]
        if not self.lookup:
            return ""
        words = re.sub(r"[^A-Za-z0-9\s-]", " ", title).split()
        q = urllib.parse.quote(f'ti:"{" ".join(words)}"')
        try:
            feed = self._get(f"https://export.arxiv.org/api/query?max_results=5&search_query={q}")
        except (OSError, ET.ParseError):
            return ""  # 网络问题不缓存，下次再试
        found = ""
        for pid, t in self._entries(feed):
            if norm(t) == key or difflib.SequenceMatcher(None, norm(t), key).ratio() >= 0.95:
                found = pid
                break
        self.cache["title"][key] = found
        self._save()
        return found

    def in_library(self, arxiv, title, text):
        if not self.lib:
            return None
        papers = self.lib["papers"]
        if arxiv and arxiv in papers:
            return arxiv
        n = norm(title)
        if n and n in self.by_title:
            return self.by_title[n]
        nt = norm(text)
        for lt, k in self.by_title.items():
            if len(lt) >= 25 and lt in nt:
                return k
        return None

    def resolve(self, bib, self_id=""):
        """→ {条目 id: {"arxiv": 编号或空, "lib": 库内键或 None}}；指向本文自己的不算库内。"""
        out = {}
        for bid, b in bib.items():
            arx = b.get("arxiv", "")
            key = self.in_library(arx, b.get("title", ""), b.get("text", ""))
            if key and not arx:
                arx = self.lib["papers"][key]["id"]
            if not arx and not key:
                arx = self.search_arxiv(b.get("title", ""))
                key = self.in_library(arx, "", "") if arx else None
            if key == self_id:
                key = None
            out[bid] = {"arxiv": arx, "lib": key}
        return out
