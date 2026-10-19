"""英文词性标注与词形还原（spaCy en_core_web_sm），给网页阅读版式用：
- 名词、专有名词、动词算「实词」，可以突出显示；
- 每个词带上原形（lemma），点一个词时，全文同一原形的词一起高亮。
spaCy 没装时，available() 返回 False，渲染会自动跳过这些功能。
"""
import functools
import re

KW_POS = {"NOUN", "PROPN", "VERB"}
MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$")
CODE = re.compile(r"`[^`]+`")
PLACE = re.compile(r"\{fn\d+\}")
WORD = re.compile(r"[A-Za-z]+")

_NLP = None
_OK = None


def available():
    global _OK
    if _OK is None:
        try:
            _model()
            _OK = True
        except Exception:
            _OK = False
    return _OK


def _model():
    global _NLP
    if _NLP is None:
        import spacy
        _NLP = spacy.load("en_core_web_sm", disable=["ner", "parser"])
    return _NLP


def norm_key(word, lemma=None):
    """高亮用的键：小写原形；全大写缩写去掉复数 s（LLMs → llm）。"""
    if re.fullmatch(r"[A-Z]{2,}s", word):
        return word[:-1].lower()
    if word.isupper() and len(word) > 1:
        return word.lower()
    return (lemma or word).lower()


def blank(s):
    """把公式、代码、占位符和 ** 换成等长空格，保持字符位置不变。"""
    sp = lambda m: " " * len(m.group(0))  # noqa: E731
    return CODE.sub(sp, MATH.sub(sp, PLACE.sub(sp, s))).replace("**", "  ")


def analyze(sentence):
    """返回句子里每个字母词的 (start, end, key, is_kw)，位置相对于原句，已跳过公式和代码。"""
    plain = blank(sentence)
    doc = _model()(plain)
    by_start = {t.idx: t for t in doc}
    out = []
    for m in WORD.finditer(plain):
        t = by_start.get(m.start())
        if t is not None and t.text[:len(m.group(0))].lower() == m.group(0).lower():
            lemma = t.lemma_ if t.lemma_ and t.lemma_ != "-PRON-" else m.group(0)
            if len(t.text) > len(m.group(0)):  # 例如 e.g. 这类 token，只取字母部分
                lemma = m.group(0)
            out.append((m.start(), m.end(), norm_key(m.group(0), lemma), t.pos_ in KW_POS))
        else:
            out.append((m.start(), m.end(), norm_key(m.group(0)), False))
    return out


@functools.lru_cache(maxsize=4096)
def term_key(term):
    """术语表条目 → 高亮键。单词用小写原形，词组用小写原文。"""
    words = re.split(r"[\s\-]+", term.strip())
    if len(words) == 1:
        w = words[0]
        if re.fullmatch(r"[A-Z]{2,}", w):
            return w.lower()
        try:
            return norm_key(w, _model()(w)[0].lemma_)
        except Exception:
            return w.lower()
    return " ".join(x.lower() for x in words)
