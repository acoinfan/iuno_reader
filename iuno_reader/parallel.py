"""按段并发调用接口：每个线程用自己的客户端（用量各记各的），结果按完成顺序交回调用方，
由调用方在主线程里汇总用量、写文件，所以文件不会被几个线程同时写。"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


def pmap(fn, items, workers, make_client):
    """对 items 里每一项调用 fn(client, item)，最多 workers 个同时进行。
    按完成顺序 yield (item, 结果, 异常或 None, 这一项用掉的用量)。"""
    local = threading.local()

    def one(item):
        if not hasattr(local, "client"):
            local.client = make_client()
        try:
            res, err = fn(local.client, item), None
        except RuntimeError as e:
            res, err = None, e
        return item, res, err, local.client.take_usage()

    if workers <= 1:  # 不开线程，方便调试
        for item in items:
            yield one(item)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, x) for x in items]):
            yield fut.result()
