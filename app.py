"""Local helpdesk HTTP app. Start with .venv/bin/python app.py."""
import argparse
import fcntl
import json
import logging
import mimetypes
import os
import re
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
from model import Classifier,evaluate
from workspace import Workspace,Conflict,restore_backup,CATEGORIES,STATUSES,PRIORITIES

ROOT=Path(__file__).resolve().parent
LOCK=threading.RLock()


def load():
    data=json.loads((ROOT/'dataset.json').read_text())
    train=[r for r in data if r['split']=='train']
    test=[r for r in data if r['split']=='test']
    assert not {r['text'] for r in train}&{r['text'] for r in test}
    model=Classifier(train)
    return model,{'train_count':len(train),'vocabulary_size':len(model.vocabulary),
                  'labels':sorted(model.docs),**evaluate(model,test)}


MODEL,REPORT=load()


class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,address,workspace):
        super().__init__(address,Handler)
        self.workspace=workspace
        self.csrf=secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    def send(self,status,body,content_type='application/json; charset=utf-8',extra=None):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        if self.path!='/classic':
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (extra or {}).items(): self.send_header(key,value)
        self.end_headers();self.wfile.write(body)

    def do_GET(self): self.dispatch('GET')
    def do_POST(self): self.dispatch('POST')
    def do_PATCH(self): self.dispatch('PATCH')
    def do_DELETE(self): self.dispatch('DELETE')

    def dispatch(self,method):
        global MODEL,REPORT
        try:
            host=self.headers.get('Host','')
            if host not in (f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'):
                return self.send(403,{'error':'仅允许本机访问'})
            parts=urlparse(self.path);path=parts.path;q=parse_qs(parts.query)
            ws=self.server.workspace
            if method=='GET':
                if path in ('/','/classic'):
                    return self.send(200,(ROOT/('classic.html' if path=='/classic' else 'index.html')).read_bytes(),'text/html; charset=utf-8')
                if path in ('/static/app.js','/static/style.css'):
                    return self.send(200,(ROOT/path.lstrip('/')).read_bytes(),'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path=='/favicon.ico': return self.send(204,b'','image/x-icon')
                if path=='/api/bootstrap': return self.send(200,{'csrf':self.server.csrf,'categories':CATEGORIES,'statuses':STATUSES,'priorities':PRIORITIES,'settings':ws.settings(),'data_dir':str(ws.root),'version':'0.2.0'})
                if path=='/api/health': return self.send(200,{'ok':True,'version':'0.2.0'})
                if path=='/api/stats': return self.send(200,ws.stats())
                if path=='/api/jobs': return self.send(200,ws.active_jobs())
                if path=='/api/models': return self.send(200,ws.model_status())
                if path == '/api/tickets/export':
                    content = ws.export_tickets(
                        q.get('q', [''])[0], q.get('status', [''])[0], q.get('category', [''])[0]
                    )
                    return self.send(200, content, 'text/csv; charset=utf-8', {
                        'Content-Disposition': 'attachment; filename="desk-tickets.csv"'
                    })
                if path=='/api/tickets': return self.send(200,ws.tickets(q.get('q',[''])[0],q.get('status',[''])[0],q.get('category',[''])[0]))
                if re.fullmatch(r'/api/tickets/\d+',path): return self.send(200,ws.ticket(int(path.split('/')[-1])))
                if path=='/api/documents': return self.send(200,ws.documents())
                if re.fullmatch(r'/api/documents/\d+',path): return self.send(200,ws.document(int(path.split('/')[-1])))
                if re.fullmatch(r'/api/documents/\d+/file',path):
                    filename,raw=ws.document_file(int(path.split('/')[3]))
                    return self.send(200,raw,'application/octet-stream',{'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename)})
                if re.fullmatch(r'/api/jobs/[a-f0-9]{32}',path): return self.send(200,ws.job(path.split('/')[-1]))
                if re.fullmatch(r'/api/backups/desk-backup-[0-9-]+-[a-f0-9]{6}\.zip',path):
                    p=ws.root/'backups'/path.split('/')[-1]
                    if not p.is_file(): raise LookupError('备份不存在')
                    return self.send(200,p.read_bytes(),'application/zip',{'Content-Disposition':'attachment; filename="'+p.name+'"'})
                if path=='/api/report': return self.send(200,REPORT)
                raise LookupError('页面不存在')
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+host: return self.send(403,{'error':'不允许跨来源操作'})
            legacy=path in ('/api/predict','/api/train')
            if not legacy and self.headers.get('X-Desk-Token')!=self.server.csrf:
                return self.send(403,{'error':'页面连接已更新，请刷新后重试'})
            if self.headers.get_content_type()!='application/json': return self.send(415,{'error':'请使用 JSON'})
            try: length=int(self.headers.get('Content-Length','0'))
            except ValueError: raise ValueError('请求长度无效') from None
            if not 0<length<=8*1024*1024: return self.send(413,{'error':'请求大小无效，文件限制5MB'})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError('请求必须是对象')
            if path=='/api/predict' and method=='POST':
                text=data.get('text','')
                if not isinstance(text,str) or not text.strip() or len(text)>2000: raise ValueError('请输入1–2000字的工单')
                return self.send(200,MODEL.predict(text))
            if path=='/api/train' and method=='POST':
                with LOCK: MODEL,REPORT=load();ws.classifier=MODEL
                return self.send(200,REPORT)
            if path=='/api/settings' and method=='POST': return self.send(200,ws.update_settings(data))
            if path=='/api/models/test' and method=='POST':
                def probe():
                    client=ws.ai_factory(ws.settings())
                    vector=client.embed(['测试本地知识库连接'])
                    answer=client.answer('连接测试','如何确认服务正常？',[{'source_id':1,'title':'连接说明','text':'完成连接测试并收到回复代表本地生成模型可用。'}])
                    return {'embedding_dimensions':len(vector[0]),'generation_ok':bool(answer['summary'])}
                return self.send(202,{'job_id':ws.submit('model_test',None,probe)})
            if path=='/api/tickets' and method=='POST': return self.send(201,ws.create_ticket(data))
            if re.fullmatch(r'/api/tickets/\d+',path):
                tid=int(path.split('/')[-1])
                if method=='PATCH': return self.send(200,ws.update_ticket(tid,data))
                if method=='DELETE': return self.send(200,ws.delete_ticket(tid))
            m=re.fullmatch(r'/api/tickets/(\d+)/(notes|analyse|feedback|solution)',path)
            if m and method=='POST':
                tid=int(m[1]);action=m[2]
                result={'notes':lambda:ws.add_note(tid,data),'analyse':lambda:ws.analyse(tid),
                        'feedback':lambda:ws.feedback(tid,data),'solution':lambda:ws.solution(tid)}[action]()
                return self.send(202 if action=='analyse' else 200,result)
            if path=='/api/documents' and method=='POST': return self.send(201,ws.save_document(data))
            if re.fullmatch(r'/api/documents/\d+',path):
                did=int(path.split('/')[-1])
                if method=='PATCH': return self.send(200,ws.save_document(data,did))
                if method=='DELETE': return self.send(200,ws.delete_document(did))
            m=re.fullmatch(r'/api/documents/(\d+)/(reindex|approve)',path)
            if m and method=='POST': return self.send(202,ws.reindex(int(m[1]),m[2]=='approve'))
            if path=='/api/search' and method=='POST':
                query=data.get('query','')
                if not isinstance(query,str) or not query.strip() or len(query)>4000: raise ValueError('请输入1–4000字的问题')
                return self.send(202,{'job_id':ws.submit('search',None,lambda:ws.search(query))})
            if path=='/api/backup' and method=='POST': return self.send(201,ws.backup())
            if path=='/api/seed' and method=='POST':
                from seed import seed
                return self.send(201,seed(ws))
            raise LookupError('接口不存在')
        except Conflict as exc: self.send(409,{'error':str(exc)})
        except LookupError as exc: self.send(404,{'error':str(exc)})
        except (ValueError,TypeError) as exc: self.send(400,{'error':str(exc)[:300]})
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            logging.exception('Request failed')
            self.send(500,{'error':'操作未完成，请重试并检查本地服务日志'})


def process_lock(data_dir):
    data_dir.mkdir(parents=True,exist_ok=True)
    handle=(data_dir/'.server.lock').open('a+')
    try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close();raise SystemExit('该数据目录已有服务运行；请先停止服务再启动或恢复备份') from None
    return handle


def main():
    parser=argparse.ArgumentParser(description='本地 AI 工单工作台')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('DESK_DATA_DIR',ROOT/'data')))
    parser.add_argument('--evaluate',action='store_true')
    parser.add_argument('--restore',type=Path)
    parser.add_argument('--seed',action='store_true')
    args=parser.parse_args()
    if args.evaluate:
        print(json.dumps(REPORT,ensure_ascii=False,indent=2));return
    lock=process_lock(args.data_dir)
    if args.restore:
        print('恢复完成：',restore_backup(args.restore,args.data_dir));lock.close();return
    ws=Workspace(args.data_dir,MODEL)
    if args.seed:
        from seed import seed
        print(seed(ws),flush=True)
    server=Server(('127.0.0.1',args.port),ws)
    def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print(f'本地 AI 工单工作台：http://127.0.0.1:{args.port}',flush=True)
    print(f'数据目录：{ws.root}',flush=True)
    try: server.serve_forever()
    finally: server.server_close();ws.close();lock.close()


if __name__=='__main__': main()
