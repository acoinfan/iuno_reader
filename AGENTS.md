# 给 Agent 的说明：iuno_reader

这个项目把 arXiv 论文处理成「逐句双语对照 + 风险标记」的结果文件夹。你可能是来**用结果**的（整理进知识库、帮人读论文），也可能是来**接手翻译或审校**的。

## 一条命令

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader run <arXiv 编号> [<编号> ...] [--sections abs,2,3,4|body|all] [--export <目录>]
# 默认 body：只翻正文（不含附录和参考文献）
```

运行后看 `out/<编号>/manifest.json`：

- `stage`：`complete`（完成）、`awaiting_translation`（等待翻译）、`awaiting_review`（等待审校）。
- `next_steps`：下一步该做什么，照做即可。
- `files`：各输出文件的路径。
- `stages.review.reviewers`：谁审的，`independence` 是 `different-model` 才算真正独立；`same-model` 表示审校者和译者是同一模型。

## 结果文件夹 out/<编号>/

| 文件 | 用途 |
|---|---|
| `paper.zh.md` | 双语对照（Obsidian）。给人读 |
| `paper.zh.html` | 双语对照（浏览器，左右两栏） |
| `paper.en.md` | 英文全文，保留章节、图注、公式、脚注。引用原文、整理笔记时用它，**不要引用译文当原文** |
| `flags.md` | 所有被标记的句子（原文、译文、原因）。人工核对时从这里开始 |
| `checked.json` | 完整数据：每段每句的原文、译文、置信度、标记 |
| `AGENT_TRANSLATE.md` / `AGENT_REVIEW.md` | 需要你接手时的任务说明 |

## 用结果时的规矩

- 引用论文内容时，以**英文原文**为准（`paper.en.md` 或 `checked.json` 里的 `en`），并注明章节或片段 id。
- 红色和橙色标记的句子，引用前必须对照原文确认。
- 「没有标记」不等于「译文正确」：要看 manifest 里有没有经过**不同模型**的审校。
- 整理进 Research-KB 时：把 `paper.zh.md` 复制为知识库的 `translations/<编号>-zh.md`，遵守知识库自己的 AGENTS.md（先拉取 D 盘、再改、提交前给用户看）。

## 接手翻译（stage = awaiting_translation）

按 `out/<编号>/AGENT_TRANSLATE.md` 做。要点：

- 译者规则在 `prompts/translator.md`，里面写了你能看到什么、看不到什么。
- 只读 `data/<编号>/tasks.json`，把译文合并进 `data/<编号>/translation.json`，**在 `__meta__.translator` 里如实写你的模型名**。
- 每段句数必须和原文一致；每句给出真实的置信度，没把握就写 `mid` 或 `low` 并说明原因，不要虚报 `high`。
- 完成后再运行一次 `run`。

## 接手审校（stage = awaiting_review）

按 `out/<编号>/AGENT_REVIEW.md` 做。要点：

- **不要和翻译交叉**：如果这篇是你翻译的，就不要再来审校；如果你和译者是同一个模型，导入时必须加 `--same-model`。
- 只读 `data/<编号>/review_tasks.json`（里面只有原文和译文，没有译者备注），不要打开 `translation.json`、不要上网找现成译文。
- 写完用 `import-review` 导入（会校验条数，并记录你审的是哪一版译文），再运行 `run`。

## 四个角色的隔离

| 角色 | 提示词 | 能看到 | 看不到 |
|---|---|---|---|
| 翻译 | `prompts/translator.md` | 英文句子、节标题、上一段原文、命中的术语 | 任何审校、回译 |
| 盲审 | `prompts/reviewer_blind.md` | 节标题、命中的术语及规定译法、逐句的英文原文与中文译文 | 译者身份、置信度和备注、其他审校 |
| 回译 | `prompts/backtranslator.md` | 只有中文译文 | 英文原文、标题、备注、审校 |
| 回译比对 | `prompts/bt_judge.md` | 英文原文、回译、命中术语的英文 | 中文译文（含术语的中文译法）、备注、审校 |

审校会拿到本段命中的术语表译法：用了规定译法就不算错（例如 few-shot → 少量示例），免得审校反复建议换成别的说法。

接口模式下，这些限制由 `iuno_reader/roles.py` 的白名单在代码里强制执行（`tests/test_roles.py` 有测试）；agent 模式下靠任务说明约束，请自觉遵守。
