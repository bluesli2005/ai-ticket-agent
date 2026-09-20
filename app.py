"""仅依赖 Python 标准库的本地教学服务器。"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from model import Classifier, evaluate

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()


def load():
    data = json.loads((ROOT/'dataset.json').read_text(encoding='utf-8'))
    train = [r for r in data if r['split'] == 'train']
    test = [r for r in data if r['split'] == 'test']
    assert not {r['text'] for r in train} & {r['text'] for r in test}, '训练测试文本重复'
    model = Classifier(train)
    return model, {'train_count': len(train), 'vocabulary_size': len(model.vocabulary),
                   'labels': sorted(model.docs), **evaluate(model, test)}


MODEL, REPORT = load()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/report':
            with LOCK:
                report = REPORT
            return self.reply(200, report)
        if self.path != '/':
            return self.reply(404, {'error': '路径不存在'})
        body = (ROOT/'index.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global MODEL, REPORT
        # 只接受同源 JSON，减少其他网页向本地接口提交请求的风险。
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': '不允许跨来源请求'})
        if self.headers.get_content_type() != 'application/json':
            return self.reply(415, {'error': '请使用 application/json'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                return self.reply(400, {'error': '请求大小需在 1–16384 字节之间'})
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('请求必须是 JSON 对象')
            if self.path == '/api/predict':
                text = payload.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                    raise ValueError('请输入 1–2000 字的工单文本')
                with LOCK:
                    result = MODEL.predict(text)
                return self.reply(200, result)
            if self.path == '/api/train':
                model, report = load()
                with LOCK:
                    MODEL, REPORT = model, report
                return self.reply(200, report)
            return self.reply(404, {'error': '路径不存在'})
        except (ValueError, UnicodeError) as exc:
            return self.reply(400, {'error': str(exc)})
        except Exception:
            return self.reply(500, {'error': '训练数据读取失败，请检查 dataset.json；原模型保持可用'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--evaluate', action='store_true')
    args = parser.parse_args()
    if args.evaluate:
        print(json.dumps(REPORT, ensure_ascii=False, indent=2))
    else:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        print(f'AI 工单分类器：http://127.0.0.1:{args.port}', flush=True)
        print(f'训练 {REPORT["train_count"]} 条；测试 {REPORT["test_count"]} 条', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
