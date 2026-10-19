"""网页前端的服务端测试（不联网、不开端口、不跑真的任务）：python tests/test_server.py"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp()
os.environ["IUNO_READER_HOME"] = os.path.join(TMP, "conf")
os.environ["IUNO_CCSWITCH_DB"] = os.path.join(TMP, "没有这个文件.db")

from iuno_reader import server  # noqa: E402
from iuno_reader.roles import ROOT  # noqa: E402

PID = "0000.00001"
D = os.path.join(ROOT, "data", PID)
JOBS_BAK = server.JOBS + ".testbak"


def call(method, path, body=None):
    code, _, data = server.handle(method, path, body)
    return code, json.loads(data or b"{}")


try:
    if os.path.exists(server.JOBS):
        shutil.copyfile(server.JOBS, JOBS_BAK)
        os.remove(server.JOBS)
    server.start_queue(run_loop=False)

    # 接口：添加后列表里只有末尾四位，没有密钥本身
    code, r = call("POST", "/api/providers", {"name": "测试家", "base": "https://example.com/v1/", "key": "sk-secret1234"})
    assert code == 200, r
    _, st = call("GET", "/api/state")
    p = next(x for x in st["providers"] if x["id"] == r["id"])
    assert p["key_tail"] == "1234" and "key" not in p and p["base"] == "https://example.com/v1"
    assert "sk-secret1234" not in json.dumps(st), "密钥不能出现在网页拿到的数据里"
    assert oct(os.stat(os.path.join(os.environ["IUNO_READER_HOME"], "providers.json")).st_mode)[-3:] == "600"
    print("接口：添加、密钥不外露、文件只有自己能读 通过")

    # 方案：内置的不能重名覆盖；自己的可以保存和删除
    code, r = call("POST", "/api/presets", {"name": "尤诺推荐", "translator": {}})
    assert code == 400, r
    mine = {"name": "我的", "scope": "body", "translator": {"provider": p["id"], "model": "m1", "thinking": "off", "workers": 3},
            "reviewer": None, "reviewer2": None}
    assert call("POST", "/api/presets", mine)[0] == 200
    _, st = call("GET", "/api/state")
    assert any(x["name"] == "我的" and not x["builtin"] for x in st["presets"])
    print("方案：保存、内置不可覆盖 通过")

    # 任务：认编号、去重；暂停、继续、删除
    code, r = call("POST", "/api/jobs", {"ids": [f"https://arxiv.org/abs/{PID} {PID}_x.pdf"], "preset": "我的"})
    assert code == 200 and r["added"] == [PID], r
    assert call("POST", "/api/jobs", {"ids": ["没有编号"], "preset": "我的"})[0] == 400
    _, st = call("GET", "/api/state")
    j = st["jobs"][-1]
    assert j["pid"] == PID and j["status"] == "queued" and j["progress"]["total"] is None
    call("POST", f"/api/jobs/{j['n']}/pause")
    assert server.Q.find(j["n"])["status"] == "paused"
    call("POST", f"/api/jobs/{j['n']}/resume")
    assert server.Q.find(j["n"])["status"] == "queued"
    print("任务：添加、暂停、继续 通过")

    # 进度和花费：直接数数据文件（正文 3 段，附录 1 段不算）
    os.makedirs(D, exist_ok=True)
    segs = [{"id": f"p{i}", "kind": "para", "section": "1 Intro", "en": "A.", "sentences": ["A."]} for i in range(3)]
    segs.append({"id": "x", "kind": "para", "section": "Appendix A More", "en": "B.", "sentences": ["B."]})
    json.dump({"title": "测试论文", "segments": segs}, open(os.path.join(D, "doc.json"), "w"))
    json.dump({"__meta__": {"translator": "deepseek-v4-pro（关思考）",
                            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 0}},
               "p0": {}, "p1": {}, "x": {}}, open(os.path.join(D, "translation.json"), "w"))
    _, st = call("GET", "/api/state")
    pr = st["jobs"][-1]["progress"]
    assert pr["title"] == "测试论文" and pr["total"] == 3 and pr["bars"][0]["done"] == 2, pr
    assert abs(pr["cost"] - 4.5) < 1e-6, pr  # 百万输入 token，空闲价 4.5 元
    code, r = call("GET", "/api/cost")
    row = next(x for x in r["rows"] if x["pid"] == PID)
    assert row["sentences"] == 3 and abs(row["total"] - 4.5) < 1e-6, row
    print("进度与花费：按正文数段、按价格表算钱 通过")

    # 批注：存进 data/<编号>/notes.json，复盘笔记写到 out/<编号>/notes.md
    note = {"id": "n1", "s": "p0-1", "side": "en", "quote": "A", "tags": ["重点"], "note": "要记住", "time": "2026-10-19 16:00"}
    assert call("POST", f"/api/notes/{PID}", {"notes": [note], "markdown": "# 批注\n- #重点 「A」"})[0] == 200
    assert call("GET", f"/api/notes/{PID}")[1]["notes"] == [note]
    md_path = os.path.join(server.OUT, PID, "notes.md")
    assert open(md_path, encoding="utf-8").read().startswith("# 批注")
    shutil.rmtree(os.path.join(server.OUT, PID), ignore_errors=True)
    print("批注：保存、读取、写复盘笔记 通过")

    call("POST", f"/api/jobs/{j['n']}/delete")
    assert server.Q.find(j["n"]) is None
    assert call("GET", "/out/../../etc/passwd")[0] == 404
    code, ctype, body = server.handle("GET", "/")
    assert code == 200 and "iuno" in body.decode()
    print("删除任务、拒绝越界路径、首页 通过")
    print("前端服务端测试全部通过")
finally:
    shutil.rmtree(D, ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)
    if os.path.exists(server.JOBS):
        os.remove(server.JOBS)
    if os.path.exists(JOBS_BAK):
        os.replace(JOBS_BAK, server.JOBS)
