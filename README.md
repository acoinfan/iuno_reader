# iuno_reader

arXiv 论文的**逐句双语对照**阅读工具：每段英文下面紧跟中文，逐句核对译文有没有丢掉否定、数字、限定词、公式和引用，并用颜色标出值得怀疑的句子。

不翻译 PDF。PDF 翻译要先拆版面再重新排版，这一步可能会悄悄丢掉整段文字（例如 BabelDOC 的 issue #620、#613）。这里直接用 arXiv 的 HTML 版（由作者上传的 LaTeX 生成；arXiv 没有时用 ar5iv 的同格式网页），段落和公式都是原样的，译文和原文可以逐句对齐、逐句检查。

## 流水线：一条命令

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader run 2602.04212 [更多编号...] [--sections abs,2,3,4] [--export <目录>]
```

自动完成：下载 arXiv HTML → 拆成段落和句子 → 翻译 → 审校 → 规则核对 → 渲染 → 写出 `out/<id>/`。

- **默认只翻正文**（摘要和所有非附录的节，外加正文引用到的图表和脚注），附录和参考文献不翻。要含附录的全文用 `--sections all`，只要某几节用 `--sections abs,2,3`。**略读**用 `--sections skim`：只翻摘要、引言，以及讨论、结论、局限、展望这类收尾的节（按节名认，「Results and Discussion」这种实验节不算），平均约为正文的三成；没有收尾节的论文只翻摘要和引言，认不出节结构的按正文全翻。附录的识别：节名以 Appendix 开头；或者节编号和前面重复（罗马数字编号的论文，附录会从 I 重新编号）。
- 配了接口（见下文「接口配置」），翻译和审校自动完成。
- 没配接口时不会失败：流水线生成任务清单和写给 agent 的说明（`AGENT_TRANSLATE.md`、`AGENT_REVIEW.md`），状态停在 `awaiting_translation` 或 `awaiting_review`。agent 按说明完成后再运行一次 `run`，会从中断处接着做。
- `--translate auto|api|agent`、`--review auto|blind|backtranslate|agent|none` 可以指定方式。
- 给其他 agent 的使用说明见 [AGENTS.md](AGENTS.md)。

### 手里不是 arXiv 编号时

`run` 和前端的输入框也认 DOI、论文页面链接和标题，会先换成 arXiv 编号再翻；只想查不想翻用 `find`：

```bash
.venv/bin/python -m iuno_reader find 10.18653/v1/2026.acl-long.366 https://aclanthology.org/2025.winlp-main.30.pdf "Emergent World Representations"
```

| 给的是 | 怎么找 arXiv 版本 |
|---|---|
| arXiv 编号、arxiv.org / ar5iv 链接、arXiv 的 DOI（`10.48550/arXiv.…`） | 直接取编号 |
| 其他 DOI | [OpenAlex](https://openalex.org) 查标题；记录里带 arXiv 版本就用它，否则拿标题去 arXiv 搜 |
| 论文页面链接（ACL Anthology、NeurIPS、期刊等；PDF 链接会换成对应页面） | 读网页里的 `citation_title`、`citation_doi`、`citation_arxiv_id` 标签，再按上面查 |
| PMC 编号或 Europe PMC 链接 | Europe PMC 接口查标题和 DOI |
| 标题 | 去 arXiv 搜，标题几乎完全一致才算 |

找不到 arXiv 版本、但有开放获取的 PDF 时，改用 PDF 翻译（见下一节）；连 PDF 也没有的，会说明查到的是哪篇、为什么翻不了。OpenReview 拦截程序请求，它的链接认不出来，请直接给标题或下载好的 PDF。查询结果缓存在 `cache/resolve.json`。

### 没有网页版时：从 PDF 翻译（准确度较低）

```bash
.venv/bin/python -m iuno_reader run /path/to/Li2024-StateAbstractionProbe.pdf --sections skim   # 本地 PDF
.venv/bin/python -m iuno_reader run 2506.02996                                                 # arXiv 和 ar5iv 都没有网页版时自动下 arXiv 的 PDF
```

- 需要 PyMuPDF：`.venv/bin/pip install pymupdf`。
- 编号：文件名里有 arXiv 编号就用它（先找网页版）；否则用文件名（去掉「06_」这类序号前缀），例如 `Li2024-StateAbstractionProbe`；从 DOI 或链接下载的用标题拼一个。PDF 放在 `cache/<编号>.pdf`，以后直接 `run <编号>` 就行。
- 拆分（`pdfextract.py`，PyMuPDF）：按栏排阅读顺序，去掉页眉页脚和侧边水印；节标题靠粗体或字号、编号顺序和常见节名认，也认 Nature 一类期刊嵌在段首的粗体标题；跨栏、跨页的段落拼回去，行尾断词查本地词典决定留不留连字符；图（图注上方）、表（表题下方）和行间公式裁成图片嵌进网页，公式不翻译；参考文献拆成条目供引用统计用，正文里的引用没有链接。
- **准确度**：网页和 Markdown 的标题下面有醒目的提醒，manifest 里有 `accuracy_note`。拆分覆盖率 = PDF 页面上正文字号的词有多少进了对照（逐词核对，不经过拼段），低于 90%、认不出节标题、没找到摘要或参考文献时另有警告。实测 NeurIPS、ACL、arXiv 预印本和 Nature 期刊各一篇，覆盖率 94%～99%。

## 网页前端

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader serve          # 浏览器打开 http://localhost:8780
```

- **任务**：把论文 PDF 拖进来（从文件名认 arXiv 编号），或粘贴编号、链接；选一个方案，点「添加」。每篇一张卡片：翻译、审校的进度条，已经花了多少钱，标红标橙句数，可以暂停、继续、重试，完成后「打开」看双语网页。
- **方案**：内置「尤诺推荐」（pro 关思考翻译 + pro 盲审）、「尤诺推荐 · 精读」（再加 Kimi K3 审）、「尤诺推荐 · 略读」（只翻摘要、引言、讨论和结论）、「最省钱」（flash 翻译，只在空闲时段跑）。也可以复制一份改成自己的：每个角色选接口、模型、思考（默认 / 关 / 低 / 高）、并发数；翻译范围（只翻正文 / 全文）；是否只在 DeepSeek 空闲时段跑。下面的「设置」可以改同时跑几篇，以及完成后把网页和 Markdown 复制到哪个目录（例如知识库的 `translations/`）。
- **接口**：自动读 cc-switch 里的接口；也可以手动添加（选服务商自动填地址）。每个接口可以「测试翻译」「测试审校」，就是跑一次 `probe`。
- **用量**：每篇的花费表（翻译、每份审校分开），按正文句数算每百句多少钱，附价格表。

只监听本机。接口密钥和自己的方案存在 `~/.config/iuno_reader/`（权限 600），网页上只显示密钥末尾四位。任务队列在 `out/jobs.json`，每个任务的日志在 `out/jobs/<编号>.log`；关掉服务再打开，没做完的任务会接着排队，已经翻好、审好的段落不会重复花钱。价格表在 `iuno_reader/providers.py`，可以用 `~/.config/iuno_reader/prices.json` 覆盖。

## 输出 out/<id>/

| 文件 | 内容 |
|---|---|
| `manifest.json` | 每一步的状态、句数和标记统计、译者和审校者及独立性、各文件路径、下一步该做什么 |
| `paper.zh.md` | 给 Obsidian 读：每段英文下面是 **译** 开头的中文；有问题的句子按颜色高亮，悬停看原因；每段下面有默认折叠的逐句对照框 |
| `paper.zh.html` | 浏览器打开，左英右中，鼠标移到一句上两边同时框出（公式需联网加载 MathJax） |
| `paper.en.md` | 英文全文，保留章节、图注、公式、脚注，给 agent 引用和整理 |
| `flags.md` | 所有被标记的句子：原文、译文、原因 |
| `checked.json` | 完整核对数据 |

## 网页阅读开关（paper.zh.html 标题区）

| 开关 | 作用 |
|---|---|
| 隐藏标记 | 关掉所有风险底色，纯阅读 |
| 字体粗细 细/中/粗 | 英文 Computer Modern 用细描边加粗，中文思源宋体切换字重；默认「中」 |
| 加粗 关/首字母/整词 | 只作用于核心词：名词、专有名词、动词、形容词、副词、数词、否定词（not、no、cannot…），以及连字符复合词和术语表多词术语的每一部分（in-context 的 in、one-token 的 one）；the、of、to、which 这类虚词不变。「首字母」加粗核心词的前几个字母（study → **st**udy，representation → **repre**sentation，1 个字母的词不加粗），默认这一档；「整词」让核心词深色稍粗、其余词变浅灰 |
| 加宽词距 | 英文栏词距再加宽（英文默认已改为左对齐，词距均匀） |
| 点词高亮 | 点任意英文单词，全文同一个词（按原形，整篇统一）一起铺底色；若在术语表里，中文栏对应译名也一起亮；最多三个，再点取消 |

另外：

- **目录**：宽屏时常驻左侧，窄屏时点右下角「目录」。列出各节、小节（中文在前，英文小字），滚动时高亮当前所在的节；底部有「图表」快捷跳转。右下角「↑」回到顶部。
- **看大图**：点图片全屏显示，再点一下看原始尺寸，Esc 关闭。
- **标签批注**：选中一段文字会出现「✎ 批注」，可以打标签（重点、不懂、疑问、生词、方法、结论，也能自己加）、写批注。有批注的句子中英两边都会加下划线和 ✎，点 ✎ 可以修改。侧栏「批注」区能按标签筛选、点击跳转，「导出复盘笔记」按节整理成 Markdown（标签写成 Obsidian 的 `#标签`）。批注存在浏览器里（按论文分开），「备份」「导入」可以换浏览器或电脑；通过 `serve` 打开时还会存到 `data/<编号>/notes.json`，并把复盘笔记写到 `out/<编号>/notes.md`（设置了「完成后复制到」时同时复制成 `<编号>-批注.md`）。

- **引用**：正文里的引用（「Lepori et al. (2026)」「[17, 39]」）中英两边都加了下划线，悬停看是哪篇，点开有标题、作者、arXiv 和出版页链接，还能跳到本文下一处引用它的地方。**在你知识库里的论文**用绿色实线加「库」字标出，卡片里写着它在库里的位置（如「cvpr4 · 精读-反面 #1」），并链到它的中文译文、PDF 和论文卡片（Obsidian）。侧栏底部列出「引用了你库里的 N 篇」，点一下跳到第一次引用的地方。Markdown 版在文末列出库内论文的双链，全部参考文献放在一个折叠框里。

- **查词与生词本**：标题区「点词：高亮 / 查词」切到查词后，点英文单词弹出词卡（音标、考试标签、中文释义、英文释义、原形）；任何模式下按住 Alt 点词也是查词。词卡上「＋ 生词本」会记下这个词的原形、释义，以及**它所在的句子（原文和译文）和出处**。收进生词本的词，在所有译文里都带淡黄色点状下划线。侧栏「生词本」可以导出 Markdown 或 Anki 用的 TSV。生词本不分论文，存在浏览器里；通过 `serve` 打开时还会存到 `data/vocab.json`，并写成知识库的 `vocab/生词本.md`（设置了「知识库目录」时）。词典查不到的词，词卡上有有道词典的链接。

开关状态保存在浏览器里。点词高亮和加粗需要 spaCy 与 `en_core_web_sm`（没有时自动关闭这两项）。

### 本地词典

查词用开源的 [ECDICT](https://github.com/skywind3000/ECDICT)（MIT 协议，七十多万词条），体积大，不进 git，自己下载一次：

```bash
mkdir -p dict && curl -L -o dict/ecdict.csv https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv
```

第一次渲染时自动转成 `dict/ecdict.db`（约 5 秒）。渲染时把这篇论文里出现的词查好嵌进网页（每篇大约多出 300KB），所以离线双击打开也能查。没有词典文件时网页里不显示「查词」。

### 引用怎么对应到论文

每条参考文献按顺序找：① 条目里自带的 arXiv 编号；② 标题和知识库里的论文对上（会议版没有 arXiv 编号时靠这一步，例如 ACL 版的 Lepori 2026 对上库里的 2602.04212）；③ 拿标题去 arXiv 搜，标题几乎完全一致才算。只查正文引用到的条目。arXiv 的查询结果缓存在 `cache/arxiv_lookup.json`，几篇同时跑时共用一把锁、请求间隔 3 秒；对应结果存在 `data/<编号>/refs.json`。

- 知识库目录：前端「设置」里的「知识库目录」，或环境变量 `IUNO_LIBRARY`（research-KB 的根目录）。会读 `pdfs/**/*.pdf`（文件名里的 arXiv 编号；`按优先级/<档>/<序号>_` 给出阅读顺序位置）、`pdfs/**/INDEX.md` 里的标题、`translations/<编号>-zh.html` 和 `papers/*.md` 卡片。没设时只做 arXiv 对应。
- 不想联网查 arXiv：`IUNO_ARXIV_LOOKUP=0`。
- 网页里链到译文、PDF 的是相对 `translations/` 目录的路径，网页放在知识库的 `translations/` 里打开才能点通；卡片用 `obsidian://` 链接打开，库名取知识库目录的名字。

### 调研 → 知识库 → 译文：kb 命令

每轮调研收的论文接进知识库，按阅读档次翻译，结果都沉淀在知识库里，一轮轮累积下去。

```bash
.venv/bin/python -m iuno_reader kb import ~/research/cvpr7/papers --project cvpr7 [--dry-run]   # 1. 论文补进知识库 pdfs/cvpr7/
# 2. 在知识库里分档：放进 pdfs/cvpr7/按优先级/1-精读-正面/01_<编号>_<简称>.pdf 这类文件夹（或 1-精读、2-细读、3-略读）
.venv/bin/python -m iuno_reader kb plan cvpr7                  # 3. 列出要翻的、估句数和花费（会下载、拆分，不调用模型）
.venv/bin/python -m iuno_reader kb translate cvpr7 --go        # 4. 按档次翻译（花钱）
.venv/bin/python -m iuno_reader kb sync                        # 5. 结果写进知识库
```

- **import**：调研目录里的 PDF、网页和 `INDEX.md` 按原来的子目录复制到 `pdfs/<项目>/`；跳过 `.venv`、`node_modules`、`text/` 和点开头的目录。已有的同名文件内容一样就跳过，内容不一样不覆盖、列出来（`INDEX.md` 例外，以调研那边为准）。
- **档次 → 方案**：精读 →「尤诺推荐 · 精读」（正文 + Kimi K3 审校）；细读 →「尤诺推荐」（正文）；略读 →「尤诺推荐 · 略读」（摘要、引言、讨论和结论）。档次从文件夹名认（`精读`、`细读`、`略读`），一篇在几个位置时取最高的。没分档的（只在分类文件夹里）默认不翻，加 `--untiered` 才按略读排进来。
- **plan** 的估价按实测单价：pro 翻译 + pro 盲审每百句约 0.6 元，K3 审校每百句约 2 元（DeepSeek 空闲时段；高峰时段 DeepSeek 翻倍）。精读论文以前没加 K3 的，也会列出来补审。没有网页版的论文先找 arXiv 版本，再不行用 PDF（准确度较低，表里注明）。
- **translate --go**：几篇同时跑（`--workers`，默认 3），每篇按方案依次翻译、审校，和前端任务队列用的是同一套步骤；日志在 `out/_kb/<编号>.log`。接口从 cc-switch 读（默认 `~/.cc-switch/cc-switch.db`）；用别的账号跑时要指定路径，例如 `IUNO_CCSWITCH_DB=/home/a_coin_fan/.cc-switch/cc-switch.db`，否则会报「找不到接口」。
- **sync** 只写工具生成的文件：`translations/<编号>-zh.md/.html`；知识库里这篇论文每一份 PDF 旁边的 `.译文.md` 跳转文件；`library/papers.tsv`（每篇一行：编号、arXiv、标题、档次、位置、译文范围、来源、审校、红橙、卡片、文件）和 `library/README.md`（各调研的统计、分了档还没翻的）；`translations/README.md` 里两个标记之间的表格（标记外面的内容不动）；`citations/` 下各调研的引用统计。
- 同一篇论文的会议版 PDF（文件名没有 arXiv 编号）按标题找到 arXiv 版本后并成一条；清单里没写标题的，读 PDF 第一页的标题。

### 引用统计：一次调研里大家都在引用什么

```bash
.venv/bin/python -m iuno_reader citations --project cvpr4   # 一个调研（知识库 pdfs/cvpr4/ 下的论文）
.venv/bin/python -m iuno_reader citations                   # 整个知识库，按调研分列，另有「跨调研共同引用」
```

把调研里每篇论文的参考文献合起来，同一篇被引论文去重（arXiv 编号；没有编号时按标题，会议版会并到有编号的那条上），数它「被我们几篇论文引用」。只用已经抓过网页的论文（`cache/<编号>.html`；不翻译也可以只 `fetch`），不调用模型。报告分四块：被引最多但不在知识库里的（可能值得补读）、被引的库内论文和它现在的阅读档次、库内论文之间谁引用了谁、跨调研共同引用（全库报告才有）。

- 报告写到知识库的 `citations/<项目>.md` 和 `citations/全库.md`（知识库里没有 `citations/` 时写到 `out/_citations/`），是生成文件，每次整个覆盖；明细 JSON 在 `out/_citations/`。
- `citations/通用论文.md` 是人人都引的通用论文名单（模型报告、Transformer、Adam 之类），名单里的不进主表，放在报告末尾的折叠框里。每行「- <arXiv 编号> · <标题>」，随时增删。
- `--min`：不在库里的论文至少被引几次才列出（默认 2）；`--top`：每张表最多几篇（默认 40）。

## 颜色

| 颜色 | 含义 | 来源 |
|---|---|---|
| 🔴 红 | 可能丢了信息：否定词、数字、公式、代码、脚注标记、引用年份对不上；限定词丢失或语气被加强（如 suggest 译成「证明」）；整句漏译；整段句数对不上 | 自动规则 |
| 🟠 橙 | 翻译没把握（翻译者自评低置信，并写明原因）；译文明显过短，可能漏译 | 翻译者自评 + 规则 |
| 🟡 黄 | 需要留意：翻译者自评中等置信；译文明显过长；残留大段英文 | 翻译者自评 + 规则 |
| 🔵 蓝 | 术语没有按术语表翻译 | 术语表 |

**这些标记只是提醒。** 标出来的句子要看一眼；没标出来的句子，也不保证完全正确。规则都是启发式的，例如「否定词」只检查译文里有没有任何否定表达，不检查否定的是不是同一件事。

## 分步命令（调试用）

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader fetch 2602.04212        # 下载 arXiv HTML 到 cache/
.venv/bin/python -m iuno_reader extract 2602.04212      # 拆成片段和句子 → data/<id>/doc.json
.venv/bin/python -m iuno_reader tasks 2602.04212 --sections 2,3,4   # 导出待译清单（人工或 Agent 翻译时用）
.venv/bin/python -m iuno_reader translate 2602.04212 --sections 2,3,4   # 自动翻译（需要接口，见下）
.venv/bin/python -m iuno_reader render 2602.04212 --translated-only     # 核对并渲染
```

- `--sections`：只处理某几节，例如 `abs,2,3,4`（`abs` 是摘要）或附录 `A,B`；`body` 表示全部正文，`all` 表示全文。`run` 默认是 `body`，分步命令默认全文。
- `--translated-only`：渲染时只输出已经翻译的片段。

### 四个角色，互不交叉

翻译、盲审、回译、回译比对各有一份提示词（`prompts/`），每份都写明「能看到什么、看不到什么」。接口模式下由 `iuno_reader/roles.py` 的白名单强制执行：回译只看得到中文、回译比对只看得到英文原文和回译、盲审看不到译者备注；每次调用都是一段新对话，不共享历史。各角色的模型分开配置，审校结果会记录它和译者是不是同一个模型（`independence`），同模型会在图例和 manifest 里注明「独立性不足」。详见 [AGENTS.md](AGENTS.md) 的表格。

### 接口配置

```bash
export IUNO_TRANSLATE_BASE_URL=... IUNO_TRANSLATE_MODEL=... IUNO_TRANSLATE_API_KEY=...   # 翻译（未设则用 IUNO_READER_*）
export IUNO_REVIEW_BASE_URL=...    IUNO_REVIEW_MODEL=...    IUNO_REVIEW_API_KEY=...      # 盲审与回译比对，建议换一家模型
export IUNO_BT_BASE_URL=...        IUNO_BT_MODEL=...        IUNO_BT_API_KEY=...          # 回译（未设则用 IUNO_REVIEW_*）
```

密钥不要写进仓库。

### 两种翻译方式

译文都存在 `data/<id>/translation.json`，格式一样：

```json
{
  "__sections__": {"2 Preliminaries: ...": "2 预备知识：..."},
  "<片段 id>": {"sentences": [{"zh": "译文", "conf": "high|mid|low", "note": "不是 high 时写原因"}]}
}
```

1. **人工或 Agent 翻译**：先 `tasks` 导出清单（每段附上上一段原文和命中的术语），按清单逐句翻译，写进上面的文件。句数必须和原文一致。
2. **自动翻译**：OpenAI 兼容接口（DeepSeek、OpenAI 等），只用 Python 标准库。设置三个环境变量后运行 `translate`：

   ```bash
   export IUNO_READER_BASE_URL=https://api.deepseek.com/v1
   export IUNO_READER_MODEL=deepseek-chat
   export IUNO_READER_API_KEY=...        # 不要写进仓库
   ```

   每翻完一段就保存一次，中途失败不会丢掉已经完成的部分；已翻译的段落默认跳过，加 `--force` 重翻。翻译规则（逐句、限定词按强弱译、公式和占位符原样保留、自评置信度）写在 `prompts/translator.md` 里。

   接口不支持 JSON 模式（`response_format`）时会自动退回普通模式；推理模型输出里的 `<think>` 段会被去掉；密钥错、模型名错这类错误不重试，直接报出状态码和返回内容。

### 换接口前先探测

```bash
.venv/bin/python -m iuno_reader probe [--roles translator,reviewer,backtranslate]
```

给每个配好接口的角色发一个三句话的固定样例（含否定、限定词、数字、公式、脚注占位符），报告能不能连上、JSON 模式支不支持、耗时和 token；翻译结果用规则核对一遍；审校角色拿到的是故意译错两句的样例（「suggests, but does not prove」译成「证明」、41.2% 写成 42.1%），看它能不能抓出来。报告另存到 `out/probe/`。

已实测（2026-10-19，`api.deepseek.com/v1`）：`deepseek-v4-flash` 翻译 3 秒、JSON 模式可用；`deepseek-v4-pro` 盲审 5 秒抓对 3/3，回译 + 比对 36 秒抓对 2/3（把第 1 句的回译措辞差异误报成 minor）。整篇论文还没有用接口跑过。

### 独立审校（规则查不出来的那一层）

规则只看表面（否定词、数字、限定词在不在），查不出「主宾译反了」「因果关系错了」这类意思上的偏差。审校环节专门补这一层，结果存成 `data/<id>/review.<审校者>.json`，渲染时自动合并：`major` 标红、`minor` 标黄，提醒里以 `[审校者]` 开头，并附建议译文。

1. **盲审**：审校者只看到原文和译文，看不到翻译者的置信度和备注。
   ```bash
   .venv/bin/python -m iuno_reader review-tasks 2602.04212            # 导出盲审清单
   # 交给人、另一个模型或子代理审，写成 review.json 后导入（会校验条数）：
   .venv/bin/python -m iuno_reader import-review 2602.04212 review.json --name blind-subagent
   # 或者用接口自动盲审：
   .venv/bin/python -m iuno_reader review 2602.04212 --mode blind
   ```
2. **回译比对**：另一个模型先**只看中文**译回英文，再在英文里比较原文和回译，回译结果也会显示在提醒里。
   ```bash
   export IUNO_REVIEW_BASE_URL=... IUNO_REVIEW_MODEL=... IUNO_REVIEW_API_KEY=...   # 建议用和翻译不同的模型
   .venv/bin/python -m iuno_reader review 2602.04212 --mode backtranslate
   ```

每条审校意见都记下了它审的是哪一版译文（`zh_reviewed`）。之后改了译文，旧意见会自动作废，不会标到新译文上。图例里会写明审了多少句、几条已作废。

**独立性要打折扣的情况**：审校者和翻译者是同一个模型（例如由同一个 Agent 另开子代理来审）时，只能算换个角度重看一遍，可能和翻译犯同样的错。要真正独立，审校要换一个不同的模型，或者由人来抽查。

### 术语表

- `glossary/terms.tsv`：术语表。可以从 Research-KB 导入：`python -m iuno_reader import-kb-glossary <glossary.md>`。
- `glossary/hedges.tsv`：限定词表。每行写明英文、可接受的中文，以及「出现了就说明语气被加强」的中文。

## 测试

```bash
.venv/bin/python tests/test_check.py                     # 核对规则的单元测试
.venv/bin/python tests/test_review.py                    # 审校合并（含过期检测、盲审清单不泄露备注）
.venv/bin/python tests/test_api_mock.py                  # 用假回复测试接口流程（不联网），含 JSON 模式退回、401 不重试
.venv/bin/python tests/test_nlp.py                       # 词性、核心词与首字母加粗长度
.venv/bin/python tests/test_server.py                    # 网页前端的服务端（接口、方案、任务队列、进度与花费）
.venv/bin/python tests/test_cites.py                     # 引用与参考文献：抽取、逐句定位（含中文）、对应知识库、引用统计；本地词典（不联网）
.venv/bin/python tests/test_resolve.py                   # 论文标识识别：arXiv、DOI、链接、PMC、标题（不联网）
.venv/bin/python tests/test_kb.py                        # kb：导入调研、总表合并与档次、计划、索引表格、跳转文件、sync 只改标记之间
.venv/bin/python tests/test_pdf.py                       # PDF 拆分：现场生成两栏 PDF，查节标题、拼段、断词、图注、参考文献、覆盖率
.venv/bin/python tests/smoke_extract.py 2602.04212 ...   # 抽取冒烟测试（先 fetch + extract）
.venv/bin/python tests/coverage_vs_pdf.py 2602.04212 <PDF 纯文本>   # 用 PDF 文本独立检查有没有漏段落
```

## 已知限制

- arXiv 自己没有 HTML 版时（2023 年 12 月以前的论文，或转换失败），自动改用 [ar5iv](https://ar5iv.labs.arxiv.org/) 由 TeX 源码转出的同格式网页；ar5iv 也转换失败的（例如 2305.15486）和不是 arXiv 的论文从 PDF 翻译，段落和节标题靠版面猜，准确度较低，见上文。
- 表格只翻译表题，表格内容请看原文。
- 图片直接引用 arXiv 上的图片地址，离线时看不到。
- 用罗马数字编号的论文（如 I、II、III），正文和附录的节编号可能重复，`--sections` 按编号选节时会同时选中两者；`body` 不受影响。
- 核对规则是启发式的：可能误报（例如把代词 one 当成数量词，已排除常见情况），也可能漏报（例如否定的对象译错了）。
- 中文栏的引用靠「作者姓 + 年份」或方括号里的数字去找，译者把作者名译成中文或改了写法时找不到（英文栏不受影响）。知识库里没有 arXiv 编号、清单里也没写标题的 PDF（例如只有 NeurIPS 版的论文）对不上。
