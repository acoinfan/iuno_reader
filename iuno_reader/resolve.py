"""把论文标识换成 arXiv 编号：arXiv 编号或链接、DOI 或 doi.org 链接、论文页面链接（ACL、OpenReview、NeurIPS、期刊）、标题。

翻译流水线只吃 arXiv（或 ar5iv）网页，所以别的标识都要先找到 arXiv 版本：
  arXiv 编号 / arxiv.org、ar5iv 链接 / arXiv 自己的 DOI（10.48550/arXiv.xxxx）  直接取编号
  其他 DOI       OpenAlex 查标题；记录里带 arXiv 版本就直接用，否则拿标题去 arXiv 搜
  论文页面链接   读网页里的 citation_title / citation_doi / citation_arxiv_id 标签，再按上面处理
  标题           去 arXiv 搜，标题几乎完全一致才算（和参考文献对应用的是同一套，见 library.Resolver）
找不到 arXiv 版本时，返回查到的标题和开放获取地址，说明为什么翻不了。结果缓存在 cache/resolve.json。
"""
import html as htmllib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .library import Resolver

ARXIV_ID = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")
ARXIV_URL = re.compile(r"(?:arxiv\.org|ar5iv(?:\.labs\.arxiv)?\.org)/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[!#-;=?-~]+)", re.I)  # 只有 ASCII（不含空格、引号、尖括号），免得吞进后面的中文标点
URL = re.compile(r"https?://[!#-;=?-~]+")
PMC = re.compile(r"\bPMC\d{5,9}\b", re.I)
_UA = {"User-Agent": "iuno_reader (paper translation tool)"}


def find_inputs(text):
    """从一段文字里找出所有论文标识；一个都没有、又像是标题时，整段当作一个标题。"""
    text = (text or "").strip()
    found, rest = [], text
    for u in URL.findall(text):
        found.append(u.rstrip(").,;"))
        rest = rest.replace(u, " ")
    for m in DOI.finditer(rest):
        found.append(m.group(1).rstrip(").,;"))
        rest = rest.replace(m.group(1), " ")
    for m in PMC.finditer(rest):
        found.append(m.group(0).upper())
        rest = rest.replace(m.group(0), " ")
    for m in ARXIV_ID.finditer(rest):
        found.append(m.group(1))
        rest = rest.replace(m.group(0), " ")
    if not found and len(re.sub(r"\W", "", rest)) >= 15:
        found.append(" ".join(rest.split()))
    return list(dict.fromkeys(found))


def _get(url, accept=None, limit=3_000_000):
    h = dict(_UA)
    if accept:
        h["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return r.read(limit), r.headers.get("content-type", ""), r.geturl()


def _meta(page):
    """论文页面里的 citation_* 标签（Google Scholar 规范，ACL、OpenReview、NeurIPS、多数期刊都有）。"""
    out = {}
    for m in re.finditer(r"<meta\s+[^>]*>", page, re.I):
        attrs = {k.lower(): next(x for x in v if x) if any(v) else ""
                 for k, *v in re.findall(r"([\w:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))", m.group(0))}
        name = attrs.get("name") or attrs.get("property")  # 属性值可能不带引号（ACL Anthology 就是）
        if name and "content" in attrs:
            out.setdefault(name.lower(), htmllib.unescape(attrs["content"]).strip())
    return out


def _landing(url):
    """PDF 链接换成对应的论文页面（PDF 里读不到标签）。"""
    u = url
    if "aclanthology.org" in u and u.endswith(".pdf"):
        u = u[:-4] + "/"
    u = re.sub(r"openreview\.net/pdf\?id=", "openreview.net/forum?id=", u)
    m = re.search(r"(papers\.nips\.cc|proceedings\.neurips\.cc)/paper_files/paper/(\d+)/file/([0-9a-f]+)-Paper(-\w+)?\.pdf", u)
    if m:
        u = f"https://{m.group(1)}/paper_files/paper/{m.group(2)}/hash/{m.group(3)}-Abstract{m.group(4) or ''}.html"
    return u


class Finder:
    def __init__(self, root=None, lookup=None):
        root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(root, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        self.arxiv = Resolver(cache_path=os.path.join(cache_dir, "arxiv_lookup.json"), lookup=lookup)
        self.path = os.path.join(cache_dir, "resolve.json")
        try:
            self.cache = json.load(open(self.path, encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def _save(self):
        try:  # 并入别的进程写进去的结果
            self.cache = {**json.load(open(self.path, encoding="utf-8")), **self.cache}
        except (OSError, ValueError):
            pass
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def find(self, item):
        """返回 {"input", "arxiv", "title", "doi", "via", "oa_url", "error"}；arxiv 为空表示没有 arXiv 版本或没查到。"""
        item = item.strip()
        m = ARXIV_URL.search(item) or ARXIV_DOI.search(item) or ARXIV_ID.fullmatch(item)
        if m:
            return {"input": item, "arxiv": m.group(1), "via": "arXiv 编号"}
        if item in self.cache and self.cache[item].get("arxiv"):
            return self.cache[item]
        r = {"input": item, "arxiv": "", "title": "", "doi": "", "via": "", "oa_url": "", "error": ""}
        try:
            if PMC.search(item):
                self._from_pmc(PMC.search(item).group(0).upper(), r)
            elif "openreview.net" in item:
                r["oa_url"], r["via"] = item, "OpenReview"
                r["error"] = "OpenReview 拦截自动请求（要人机验证），请直接给论文标题"
            elif URL.fullmatch(item):
                self._from_page(item, r)
            elif DOI.fullmatch(item) or item.lower().startswith("doi:"):
                self._from_doi(DOI.search(item).group(1), r)
            else:
                r["title"] = item
            if not r["arxiv"] and r["title"]:
                r["arxiv"] = self.arxiv.search_arxiv(r["title"])
                if r["arxiv"]:
                    r["via"] = (r["via"] + " → " if r["via"] else "") + "按标题在 arXiv 找到"
        except (OSError, ValueError) as e:  # 网络问题不缓存，下次再试
            r["error"] = f"{type(e).__name__}: {e}"
            return r
        if not r["arxiv"] and not r["error"]:
            r["error"] = "arXiv 上没有标题一致的论文" if r["title"] else "认不出这是哪篇论文"
        self.cache[item] = r
        self._save()
        return r

    def _from_doi(self, doi, r):
        r["doi"] = doi
        m = ARXIV_DOI.search(doi)
        if m:
            r["arxiv"], r["via"] = m.group(1), "arXiv 的 DOI"
            return
        q = urllib.parse.quote(doi, safe="/")
        try:
            w = json.loads(_get(f"https://api.openalex.org/works/doi:{q}?select=title,locations,best_oa_location")[0])
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            w = {}
        r["title"] = w.get("title") or ""
        r["via"] = "DOI → OpenAlex 查到标题" if r["title"] else "DOI（OpenAlex 没有记录）"
        for loc in w.get("locations") or []:
            m = ARXIV_URL.search(loc.get("landing_page_url") or "") or ARXIV_URL.search(loc.get("pdf_url") or "")
            if m:
                r["arxiv"], r["via"] = m.group(1), "DOI → OpenAlex 记录里的 arXiv 版本"
                return
        oa = w.get("best_oa_location") or {}
        r["oa_url"] = oa.get("pdf_url") or oa.get("landing_page_url") or ""

    def _from_pmc(self, pmcid, r):
        """PubMed Central 的论文（神经科学、生物类期刊）：Europe PMC 接口查标题和 DOI。它的网页会拒绝程序请求。"""
        q = urllib.parse.quote(f"PMCID:{pmcid}")
        res = json.loads(_get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}&format=json&resultType=lite")[0])
        hit = (res.get("resultList", {}).get("result") or [{}])[0]
        r["title"] = (hit.get("title") or "").rstrip(".")
        r["doi"], r["via"] = hit.get("doi", ""), "PMC 编号 → Europe PMC 查到标题"
        r["oa_url"] = f"https://europepmc.org/article/PMC/{pmcid}"

    def _from_page(self, url, r):
        page_url = _landing(url)
        data, ctype, final = _get(page_url)
        if "pdf" in ctype.lower() or data[:5] == b"%PDF-":
            r["oa_url"], r["via"] = url, "PDF 链接（读不到标题）"
            m = re.search(r"(10\.\d{4,9}/[\d.]*\d)(?:v\d+)?(?:\.full)?(?:\.pdf)?", url)  # bioRxiv 这类链接里带着 DOI
            if m:
                self._from_doi(m.group(1), r)
                r["oa_url"], r["via"] = url, "链接里的 DOI → " + r["via"]
            return
        page = data.decode("utf-8", "replace")
        meta = _meta(page)
        r["title"] = meta.get("citation_title") or meta.get("dc.title") or ""
        r["via"] = "论文页面的标签"
        if not r["title"]:  # 没有论文标签的网页文章（如 transformer-circuits.pub），退而用网页标题
            t = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            r["title"] = meta.get("og:title") or (htmllib.unescape(" ".join(t.group(1).split())) if t else "")
            r["via"] = "网页标题"
        arx = meta.get("citation_arxiv_id", "")
        if ARXIV_ID.fullmatch(arx):
            r["arxiv"], r["via"] = arx, "论文页面里写的 arXiv 编号"
            return
        doi = meta.get("citation_doi") or meta.get("dc.identifier", "")
        if DOI.search(doi or ""):
            title = r["title"]
            self._from_doi(DOI.search(doi).group(1), r)
            r["title"] = r["title"] or title
            r["via"] = "论文页面 → " + r["via"]
        r["oa_url"] = r["oa_url"] or meta.get("citation_pdf_url") or url
