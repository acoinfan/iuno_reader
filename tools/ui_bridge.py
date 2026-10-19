"""开发用：在进程内跑前端服务（任务队列 + 接口），从标准输入逐行读请求、往标准输出写响应，不开端口。
给 tools/ui_shot.py 截图用（截图用的浏览器把页面请求转到这里）。
请求：{"method": "GET", "path": "/api/state", "body": null}；响应：{"code": 200, "ctype": "...", "b64": "..."}"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iuno_reader import server  # noqa: E402

server.start_queue(run_loop=True)
for line in sys.stdin:
    req = json.loads(line)
    code, ctype, data = server.handle(req["method"], req["path"], req.get("body"))
    sys.stdout.write(json.dumps({"code": code, "ctype": ctype, "b64": base64.b64encode(data).decode()}) + "\n")
    sys.stdout.flush()
