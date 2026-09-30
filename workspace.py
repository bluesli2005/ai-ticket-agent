"""Persistent single-user helpdesk, document ingestion, local RAG and backup."""
import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from ai import Ollama, AIUnavailable, rank, tokens, validate_endpoint

CATEGORIES=['账号与认证','权限与访问','支付与账单','网络与连接','应用与数据故障','功能与改进需求','待人工分类']
LEGACY_CATEGORIES={'账号登录':'待人工分类','支付账单':'支付与账单','系统故障':'待人工分类','功能需求':'功能与改进需求','其他':'待人工分类'}
STATUSES=['待处理','处理中','已解决','已关闭']
PRIORITIES=['低','普通','高','紧急']
DEFAULTS={'endpoint':'http://127.0.0.1:11434','chat_model':'qwen2.5:3b',
          'embed_model':'qwen3-embedding:0.6b'}
MAX_FILE=5*1024*1024


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def js(value): return json.dumps(value,ensure_ascii=False)


class Conflict(ValueError): pass


def required(value, label, limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError(f'{label}不能为空，且不超过 {limit} 字')
    return value.strip()


class Workspace:
    def __init__(self, data_dir, classifier, ai_factory=Ollama):
        self.root=Path(data_dir); self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'workspace.sqlite3'
        self.lock=threading.RLock()
        self.classifier=classifier
        self.ai_factory=ai_factory
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='desk-job')
        with self.connect() as con:
            con.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tickets(
              id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,description TEXT NOT NULL,
              category TEXT NOT NULL,priority TEXT NOT NULL,status TEXT NOT NULL DEFAULT '待处理',
              requester TEXT NOT NULL DEFAULT '',resolution TEXT NOT NULL DEFAULT '',reply TEXT NOT NULL DEFAULT '',
              version INTEGER NOT NULL DEFAULT 1,sample INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status,updated_at);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id,id);
            CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,filename TEXT NOT NULL,raw BLOB NOT NULL,content TEXT NOT NULL DEFAULT '',
              digest TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,approved INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'queued',error TEXT NOT NULL DEFAULT '',progress INTEGER NOT NULL DEFAULT 0,
              embed_model TEXT NOT NULL DEFAULT '',source_ticket INTEGER, sample INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_documents_digest ON documents(digest);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
              revision INTEGER NOT NULL,ordinal INTEGER NOT NULL,page INTEGER,text TEXT NOT NULL,vector TEXT);
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id,revision);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,target_id INTEGER,
              status TEXT NOT NULL,result TEXT,error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY,ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
              ticket_version INTEGER NOT NULL,result TEXT NOT NULL,feedback TEXT,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_analyses_ticket ON analyses(ticket_id,id);
            PRAGMA user_version=1;
            ''')
            for key,value in DEFAULTS.items():
                con.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,value))
            con.execute("UPDATE jobs SET status='failed',error='上次任务因服务关闭而中断，请重试' WHERE status IN ('queued','running')")
            con.execute("UPDATE documents SET status='failed',error='导入中断，请重新索引' WHERE status IN ('queued','parsing','indexing')")

        self.migrate_categories()

    def migrate_categories(self):
        # Back up before changing historical labels. Splits require human review.
        with self.lock,self.connect() as con:
            rows=con.execute('SELECT id,category FROM tickets').fetchall()
            rows=[r for r in rows if r['category'] in LEGACY_CATEGORIES]
            if not rows: return
            self.backup()
            for row in rows:
                category=LEGACY_CATEGORIES[row['category']]
                con.execute('UPDATE tickets SET category=?,version=version+1,updated_at=? WHERE id=?',
                            (category,now(),row['id']))
                self.event(con,row['id'],'分类迁移',f"原类别：{row['category']} → {category}；分类体系升级，历史分析需重新生成")

    @contextmanager
    def connect(self):
        con=sqlite3.connect(self.db,timeout=15)
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def settings(self):
        with self.connect() as con:
            return {r['key']:r['value'] for r in con.execute('SELECT * FROM settings') if r['key'] in DEFAULTS}

    def update_settings(self, data):
        values={k:required(data.get(k),'模型配置',160) for k in DEFAULTS}
        values['endpoint']=validate_endpoint(values['endpoint'])
        if any('cloud' in values[k].lower() for k in ('chat_model','embed_model')):
            raise ValueError('本版本仅使用本地模型，请选择不含 cloud 的模型')
        with self.lock,self.connect() as con:
            old=self.settings()
            for key,value in values.items():
                con.execute('UPDATE settings SET value=? WHERE key=?',(value,key))
            changed=any(old[k]!=values[k] for k in ('embed_model','endpoint'))
            if changed:
                con.execute('UPDATE chunks SET vector=NULL')
                con.execute("UPDATE documents SET embed_model='',status='lexical',error='向量模型已更改，请重新索引' WHERE status='ready'")
        return {'settings':values,'reindex_required':changed}

    def model_status(self):
        settings=self.settings()
        try:
            models=self.ai_factory(settings).models()
            return {'online':True,'models':models,'chat_ready':settings['chat_model'] in models,
                    'embed_ready':settings['embed_model'] in models}
        except AIUnavailable as exc:
            return {'online':False,'models':[],'chat_ready':False,'embed_ready':False,'error':str(exc)}

    def event(self, con, tid, kind, body):
        con.execute('INSERT INTO events(ticket_id,kind,body,created_at) VALUES (?,?,?,?)',(tid,kind,body,now()))

    def tickets(self, search='',status='',category='', *, limit=500):
        query='SELECT * FROM tickets WHERE 1=1';args=[]
        if search:
            query+=' AND (title LIKE ? OR description LIKE ? OR CAST(id AS TEXT)=?)'
            args.extend(['%'+search+'%','%'+search+'%',search.lstrip('#')])
        if status: query+=' AND status=?';args.append(status)
        if category: query+=' AND category=?';args.append(category)
        with self.connect() as con:
            query += ' ORDER BY updated_at DESC,id DESC'
            if limit is not None:
                query += ' LIMIT ?'
                args.append(limit)
            return [dict(r) for r in con.execute(query, args)]

    def export_tickets(self, search='', status='', category=''):
        """Export the complete applied filter, independent of the UI's 500-row cap."""
        columns = [
            ('id', '工单编号'), ('title', '标题'), ('description', '问题描述'),
            ('category', '类别'), ('priority', '优先级'), ('status', '状态'),
            ('requester', '提交人'), ('reply', '回复'), ('resolution', '解决方案'),
            ('created_at', '创建时间（UTC）'), ('updated_at', '更新时间（UTC）'),
        ]
        output = io.StringIO(newline='')
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow([label for _, label in columns])
        for ticket in self.tickets(search, status, category, limit=None):
            values = []
            for key, _ in columns:
                value = str(ticket[key] if ticket[key] is not None else '')
                # Quote formula-like cells as text, including leading whitespace.
                if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
                    value = "'" + value
                values.append(value)
            writer.writerow(values)
        return output.getvalue().encode('utf-8-sig')

    def ticket(self,tid):
        with self.connect() as con:
            row=con.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
            if not row: raise LookupError('工单不存在')
            result=dict(row)
            result['events']=[dict(r) for r in con.execute('SELECT * FROM events WHERE ticket_id=? ORDER BY id DESC',(tid,))]
            a=con.execute('SELECT * FROM analyses WHERE ticket_id=? ORDER BY id DESC LIMIT 1',(tid,)).fetchone()
            result['analysis']=None
            if a:
                analysis={**dict(a),'result':json.loads(a['result'])}
                analysis['stale']=a['ticket_version']!=row['version']
                for source in analysis['result'].get('sources',[]):
                    current=con.execute('SELECT revision,approved,status FROM documents WHERE id=?',(source['doc_id'],)).fetchone()
                    source['current']=bool(current and current['approved'] and current['revision']==source['revision']
                        and current['status'] in ('ready','lexical'))
                    if not source['current']: analysis['stale']=True
                result['analysis']=analysis
            return result

    def create_ticket(self,data):
        title=required(data.get('title'),'标题',160)
        body=required(data.get('description'),'问题描述',12000)
        category=data.get('category') or self.classifier.predict(title+' '+body)['label']
        category=LEGACY_CATEGORIES.get(category,category)
        if category not in CATEGORIES: category='待人工分类'
        priority=data.get('priority','普通')
        if priority not in PRIORITIES: raise ValueError('优先级无效')
        requester=data.get('requester','')
        if not isinstance(requester,str) or len(requester)>100: raise ValueError('提交人不超过100字')
        with self.lock,self.connect() as con:
            tid=con.execute('INSERT INTO tickets(title,description,category,priority,requester,sample,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                (title,body,category,priority,requester,int(bool(data.get('sample'))),now(),now())).lastrowid
            self.event(con,tid,'创建','创建工单')
        return self.ticket(tid)

    def update_ticket(self,tid,data):
        with self.lock,self.connect() as con:
            ticket=con.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
            if not ticket: raise LookupError('工单不存在')
            if data.get('version')!=ticket['version']: raise Conflict('工单已变化，请刷新后再保存')
            value=dict(ticket)
            for key in ['title','description','category','priority','status','requester','resolution','reply']:
                if key in data: value[key]=data[key]
            value['title']=required(value['title'],'标题',160)
            value['description']=required(value['description'],'问题描述',12000)
            value['category']=LEGACY_CATEGORIES.get(value['category'],value['category'])
            if value['category'] not in CATEGORIES or value['priority'] not in PRIORITIES or value['status'] not in STATUSES:
                raise ValueError('类别、状态或优先级无效')
            for key in ['requester','resolution','reply']:
                if not isinstance(value[key],str) or len(value[key])>16000: raise ValueError('字段格式或长度无效')
            if value['status'] in ('已解决','已关闭') and not value['resolution'].strip():
                raise ValueError('请填写解决方案后再解决或关闭工单')
            if value['status']=='已关闭' and ticket['status'] not in ('已解决','已关闭'):
                raise ValueError('请先将工单标记为已解决')
            if data.get('analysis_id'):
                a=self.ticket(tid)['analysis']
                if not a or a['id']!=data['analysis_id'] or a['stale']:
                    raise Conflict('工单或引用资料已变化，请重新分析后采纳')
                feedback='accepted' if value['reply']==a['result']['reply'] else 'edited'
                con.execute('UPDATE analyses SET feedback=? WHERE id=?',(feedback,a['id']))
                self.event(con,tid,'反馈','采纳 AI 草稿' if feedback=='accepted' else '修改后采纳 AI 草稿')
            keys=['title','description','category','priority','status','requester','resolution','reply']
            changed=[k for k in keys if value[k]!=ticket[k]]
            if not changed: return self.ticket(tid)
            con.execute('UPDATE tickets SET '+','.join(k+'=?' for k in keys)+',version=version+1,updated_at=? WHERE id=?',
                        [value[k] for k in keys]+[now(),tid])
            message=f"状态：{ticket['status']} → {value['status']}" if 'status' in changed else '更新工单内容'
            self.event(con,tid,'更新',message)
        return self.ticket(tid)

    def add_note(self,tid,data):
        text=required(data.get('body'),'处理记录',8000)
        with self.lock,self.connect() as con:
            if not con.execute('SELECT 1 FROM tickets WHERE id=?',(tid,)).fetchone(): raise LookupError('工单不存在')
            self.event(con,tid,'记录',text)
        return self.ticket(tid)

    def delete_ticket(self,tid):
        with self.lock,self.connect() as con:
            con.execute('DELETE FROM tickets WHERE id=?',(tid,))
            # Approved knowledge is independent of the source ticket and intentionally retained.
            con.execute('UPDATE documents SET source_ticket=NULL WHERE source_ticket=?',(tid,))
        return {'ok':True}

    def documents(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute('''SELECT d.id,d.title,d.filename,d.revision,d.approved,d.status,d.error,
              d.progress,d.embed_model,d.source_ticket,d.sample,d.created_at,d.updated_at,
              (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunks
              FROM documents d ORDER BY d.updated_at DESC,d.id DESC''')]

    def document(self,did):
        with self.connect() as con:
            row=con.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
            if not row: raise LookupError('文档不存在')
            result=dict(row);result.pop('raw')
            result['chunks']=[dict(r) for r in con.execute('SELECT id,ordinal,page,text FROM chunks WHERE doc_id=? ORDER BY ordinal',(did,))]
            return result

    def document_file(self,did):
        with self.connect() as con:
            r=con.execute('SELECT filename,raw FROM documents WHERE id=?',(did,)).fetchone()
            if not r: raise LookupError('文档不存在')
            return r['filename'],r['raw']

    def submit(self,kind,target,fn):
        jid=uuid.uuid4().hex
        with self.connect() as con:
            con.execute('INSERT INTO jobs(id,kind,target_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                        (jid,kind,target,'queued',now(),now()))
        def run():
            with self.connect() as con: con.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?",(now(),jid))
            try:
                result=fn()
                with self.connect() as con: con.execute("UPDATE jobs SET status='done',result=?,updated_at=? WHERE id=?",(js(result),now(),jid))
            except Exception as exc:
                logging.warning('Job %s failed: %s',jid,exc)
                with self.connect() as con: con.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:500],now(),jid))
        self.pool.submit(run)
        return jid

    def active_jobs(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT id,kind,target_id,status FROM jobs WHERE status IN ('queued','running') ORDER BY created_at")]

    def job(self,jid):
        with self.connect() as con:
            row=con.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not row: raise LookupError('任务不存在')
            result=dict(row)
            if result['result']: result['result']=json.loads(result['result'])
            return result

    def save_document(self,data,did=None):
        title=required(data.get('title'),'文档标题',160)
        filename=data.get('filename',title+'.md')
        if not isinstance(filename,str): raise ValueError('文件名无效')
        filename=Path(filename).name
        if Path(filename).suffix.lower() not in ('.md','.txt','.pdf'): raise ValueError('仅支持 Markdown、TXT 和文本型 PDF')
        if 'file_base64' in data:
            try: raw=base64.b64decode(data['file_base64'],validate=True)
            except Exception: raise ValueError('上传文件编码无效') from None
        else:
            text=required(data.get('content'),'文档内容',500000)
            raw=text.encode('utf-8');filename=Path(filename).stem+'.md'
        if not raw or len(raw)>MAX_FILE: raise ValueError('文件不能为空，且不超过5MB')
        digest=hashlib.sha256(raw).hexdigest()
        approved=0 if data.get('review') else 1
        with self.lock,self.connect() as con:
            if did:
                old=con.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
                if not old: raise LookupError('文档不存在')
                if data.get('revision')!=old['revision']: raise Conflict('文档已变化，请刷新后再保存')
                approved=old['approved'];revision=old['revision']+1
                con.execute('DELETE FROM chunks WHERE doc_id=?',(did,))
                con.execute('UPDATE documents SET title=?,filename=?,raw=?,content=?,digest=?,revision=?,status=?,progress=0,error=?,embed_model=?,updated_at=? WHERE id=?',
                    (title,filename,raw,raw.decode('utf-8') if 'file_base64' not in data else '',digest,revision,
                     'queued' if approved else 'review','','',now(),did))
            else:
                duplicate=con.execute('SELECT id FROM documents WHERE digest=?',(digest,)).fetchone()
                if duplicate: raise Conflict(f"内容已存在于文档 #{duplicate['id']}，请更新原文档")
                revision=1
                did=con.execute('INSERT INTO documents(title,filename,raw,content,digest,approved,status,source_ticket,sample,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  (title,filename,raw,raw.decode('utf-8') if 'file_base64' not in data else '',digest,approved,
                   'queued' if approved else 'review',data.get('source_ticket'),int(bool(data.get('sample'))),now(),now())).lastrowid
        jid=self.submit('index',did,lambda:self.index_document(did,revision)) if approved else None
        return {'id':did,'job_id':jid,'status':'queued' if approved else 'review'}

    def reindex(self,did,approve=False):
        with self.lock,self.connect() as con:
            doc=con.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
            if not doc: raise LookupError('文档不存在')
            if not doc['approved'] and not approve: raise ValueError('请先审核并发布该知识条目')
            rev=doc['revision']+1
            con.execute('DELETE FROM chunks WHERE doc_id=?',(did,))
            con.execute("UPDATE documents SET revision=?,approved=1,status='queued',progress=0,error='',updated_at=? WHERE id=?",(rev,now(),did))
        return {'job_id':self.submit('index',did,lambda:self.index_document(did,rev))}

    def delete_document(self,did):
        with self.lock,self.connect() as con: con.execute('DELETE FROM documents WHERE id=?',(did,))
        return {'ok':True}

    def index_document(self,did,revision):
        def progress(status,percent,error=''):
            with self.lock,self.connect() as con:
                con.execute('UPDATE documents SET status=?,progress=?,error=? WHERE id=? AND revision=?',(status,percent,error,did,revision))
        try:
            with self.connect() as con:
                doc=con.execute('SELECT * FROM documents WHERE id=? AND revision=?',(did,revision)).fetchone()
            if not doc: return {'superseded':True}
            settings=self.settings();client=self.ai_factory(settings)
            progress('parsing',10)
            pages=[]
            if Path(doc['filename']).suffix.lower()=='.pdf':
                try: from pypdf import PdfReader
                except ImportError: raise ValueError('PDF 解析依赖未安装，请使用项目虚拟环境启动') from None
                pdf=PdfReader(io.BytesIO(doc['raw']))
                if pdf.is_encrypted: raise ValueError('暂不支持加密 PDF')
                if len(pdf.pages)>200: raise ValueError('PDF 请拆分为每份200页以内')
                pages=[(i+1,p.extract_text() or '') for i,p in enumerate(pdf.pages)]
            else:
                try: pages=[(None,doc['raw'].decode('utf-8-sig'))]
                except UnicodeError: raise ValueError('文本请使用 UTF-8 编码') from None
            if not any(text.strip() for _,text in pages): raise ValueError('未提取到文字；扫描 PDF 需要先做 OCR')
            if sum(len(text) for _,text in pages)>500000: raise ValueError('文档文字过多，请拆分后导入')
            chunks=[]
            for page,text in pages:
                text=text.strip()
                # Fixed overlapping chunks preserve exact source strings and PDF page boundaries.
                for start in range(0,len(text),500):
                    part=text[start:start+650].strip()
                    if part: chunks.append({'page':page,'text':part})
            progress('indexing',35)
            vectors=[];warning=''
            try:
                for start in range(0,len(chunks),8):
                    vectors.extend(client.embed([c['text'] for c in chunks[start:start+8]]))
                    progress('indexing',35+int(60*len(vectors)/len(chunks)))
            except AIUnavailable as exc:
                vectors=[];warning=str(exc)+'；当前可使用关键词检索，服务恢复后请重新索引'
            with self.lock,self.connect() as con:
                current=con.execute('SELECT revision FROM documents WHERE id=?',(did,)).fetchone()
                if not current or current['revision']!=revision: return {'superseded':True}
                if any(self.settings()[k]!=settings[k] for k in ('endpoint','embed_model')):
                    vectors=[];warning='向量模型配置在索引期间发生变化，请重新索引'
                con.execute('DELETE FROM chunks WHERE doc_id=?',(did,))
                con.executemany('INSERT INTO chunks(doc_id,revision,ordinal,page,text,vector) VALUES (?,?,?,?,?,?)',
                    [(did,revision,i+1,c['page'],c['text'],js(vectors[i]) if vectors else None) for i,c in enumerate(chunks)])
                con.execute('UPDATE documents SET content=?,status=?,progress=100,error=?,embed_model=?,updated_at=? WHERE id=?',
                    ('\n\n'.join(t for _,t in pages),'ready' if vectors else 'lexical',warning,
                     settings['embed_model'] if vectors else '',now(),did))
            return {'doc_id':did,'chunks':len(chunks),'semantic':bool(vectors),'warning':warning}
        except Exception as exc:
            progress('failed',0,str(exc)[:400]);raise

    def search(self,query):
        query=required(query,'检索问题',4000)
        settings=self.settings();vector=None;warning=''
        with self.connect() as con:
            rows=[]
            for r in con.execute('''SELECT c.id AS chunk_id,c.doc_id,c.revision,c.ordinal,c.page,c.text,c.vector,
                 d.title,d.embed_model,d.source_ticket FROM chunks c JOIN documents d ON d.id=c.doc_id
                 WHERE d.approved=1 AND d.status IN ('ready','lexical') AND c.revision=d.revision'''):
                row=dict(r)
                row['vector']=json.loads(row['vector']) if row['vector'] and row['embed_model']==settings['embed_model'] else None
                rows.append(row)
        if any(r['vector'] for r in rows):
            try: vector=self.ai_factory(settings).embed([query])[0]
            except AIUnavailable as exc: warning=str(exc)+'；已使用关键词检索'
        elif rows: warning='当前索引尚未包含语义向量，使用关键词检索'
        # Re-read current revisions after embedding so a concurrent deletion cannot leak stale evidence.
        with self.connect() as con:
            live={r['id']:r['revision'] for r in con.execute("SELECT id,revision FROM documents WHERE approved=1 AND status IN ('ready','lexical')")}
        rows=[r for r in rows if live.get(r['doc_id'])==r['revision']]
        if any(self.settings()[k]!=settings[k] for k in ('endpoint','embed_model')):
            vector=None;warning='检索期间模型配置变化，已使用关键词检索'
        return {'sources':rank(query,rows,vector),'mode':'hybrid' if vector else 'lexical','warning':warning}

    def analyse(self,tid):
        self.ticket(tid)
        with self.lock,self.connect() as con:
            existing=con.execute("SELECT id FROM jobs WHERE target_id=? AND kind='analysis' AND status IN ('queued','running')",(tid,)).fetchone()
            if existing: return {'job_id':existing['id']}
            return {'job_id':self.submit('analysis',tid,lambda:self.run_analysis(tid))}

    def run_analysis(self,tid):
        ticket=self.ticket(tid)
        query=ticket['title']+'\n'+ticket['description']
        predicted=self.classifier.predict(query)
        urgent=any(word in query for word in ['全员','全部无法','数据丢失','泄露','生产中断'])
        retrieved=self.search(query[:4000]);sources=retrieved['sources']
        result={'summary':ticket['title'],'category':predicted['label'] if predicted['label'] in CATEGORIES else '待人工分类',
                'category_reason':'现有样本分类器的建议，需人工确认；不是校准概率',
                'priority':'高' if urgent else '普通',
                'priority_reason':'描述涉及广泛影响或数据风险，请人工核实' if urgent else '未识别到广泛影响描述，请补充影响范围',
                'missing_info':['发生时间、使用设备和软件版本','完整错误提示及影响范围'],
                'steps':[],'reply':'现有知识库不足以确认处理办法，请补充错误提示、设备版本和发生时间，由人工继续排查。',
                'sources':sources,'mode':'no_evidence','retrieval_mode':retrieved['mode'],
                'warning':retrieved['warning'],'answerable':False,'needs_review':True,'model':None}
        if sources:
            try:
                generation_settings=self.settings()
                ai=self.ai_factory(generation_settings)
                answer=ai.answer(ticket['title'],ticket['description'],sources)
                result.update(answer)
                result['summary']=ticket['title']
                result['mode']='generated' if answer['answerable'] else 'insufficient'
                result['model']=generation_settings['chat_model']
                if not answer['answerable']:
                    result['steps']=[]
                    result['reply']='现有资料不足以支持具体处理建议。请补充以下信息：'+ '；'.join(answer['missing_info'])
                else:
                    used=sorted({i for step in answer['steps'] for i in step['source_ids']})
                    result['reply']+='\n\n参考资料：'+' '.join(f'[{i}]' for i in used)
            except AIUnavailable as exc:
                result['mode']='extractive';result['warning']=str(exc)
                result['reply']='已找到相关资料，但本地生成模型暂不可用。请由人工核对下方原文后编写回复。'
                result['steps']=[]
        with self.connect() as con:
            candidates=[dict(r) for r in con.execute("SELECT id,title,resolution FROM tickets WHERE id!=? AND status IN ('已解决','已关闭')",(tid,))]
        qtokens=set(tokens(query))
        result['similar_tickets']=sorted([{**r,'similarity':len(qtokens & set(tokens(r['title']+' '+r['resolution'])))/max(1,len(qtokens))}
            for r in candidates],key=lambda r:r['similarity'],reverse=True)
        result['similar_tickets']=[r for r in result['similar_tickets'] if r['similarity']>=0.1][:3]
        with self.lock,self.connect() as con:
            current=con.execute('SELECT version FROM tickets WHERE id=?',(tid,)).fetchone()
            if not current: raise ValueError('工单在分析期间被删除')
            aid=con.execute('INSERT INTO analyses(ticket_id,ticket_version,result,created_at) VALUES (?,?,?,?)',
                (tid,ticket['version'],js(result),now())).lastrowid
            self.event(con,tid,'分析','完成本地分析，等待人工核对' if result['mode']=='generated' else '完成资料检索，需人工继续处理')
        return {'analysis_id':aid,'ticket_id':tid,'mode':result['mode']}

    def feedback(self,tid,data):
        feedback=data.get('feedback')
        if feedback not in ('accepted','edited','rejected'): raise ValueError('反馈类型无效')
        with self.lock,self.connect() as con:
            t=self.ticket(tid);a=t['analysis']
            if not a or data.get('analysis_id')!=a['id']: raise Conflict('分析结果已更新，请刷新')
            if feedback!='rejected' and a['stale']: raise Conflict('工单或引用资料已变化，请重新分析后采纳')
            con.execute('UPDATE analyses SET feedback=? WHERE id=?',(feedback,a['id']))
            self.event(con,tid,'反馈',{'accepted':'采纳 AI 草稿','edited':'修改后采纳 AI 草稿','rejected':'拒绝 AI 建议'}[feedback])
        return self.ticket(tid)

    def solution(self,tid):
        t=self.ticket(tid)
        if t['status'] not in ('已解决','已关闭') or not t['resolution'].strip(): raise ValueError('请先填写解决方案并将工单标记为已解决')
        text=f"# {t['title']}\n\n## 问题现象\n{t['description']}\n\n## 已确认的解决方案\n{t['resolution']}\n\n来源：工单 #{tid}\n"
        return self.save_document({'title':t['title']+' · 解决方案','content':text,'review':True,'source_ticket':tid})

    def stats(self):
        with self.connect() as con:
            statuses={s:0 for s in STATUSES}
            statuses.update({r[0]:r[1] for r in con.execute('SELECT status,COUNT(*) FROM tickets GROUP BY status')})
            categories=[{'name':r[0],'count':r[1]} for r in con.execute('SELECT category,COUNT(*) FROM tickets GROUP BY category ORDER BY COUNT(*) DESC')]
            return {'statuses':statuses,'total':sum(statuses.values()),'categories':categories,
                    'documents':con.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
                    'ready_documents':con.execute("SELECT COUNT(*) FROM documents WHERE status='ready'").fetchone()[0],
                    'review_documents':con.execute('SELECT COUNT(*) FROM documents WHERE approved=0').fetchone()[0],
                    'active_jobs':con.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0],
                    'feedback':[{'type':r[0],'count':r[1]} for r in con.execute('SELECT feedback,COUNT(*) FROM analyses WHERE feedback IS NOT NULL GROUP BY feedback')]}

    def backup(self):
        name='desk-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.zip'
        destination=self.root/'backups';destination.mkdir(exist_ok=True)
        temp=destination/(uuid.uuid4().hex+'.sqlite3')
        try:
            with self.lock,self.connect() as source,closing(sqlite3.connect(temp)) as target: source.backup(target)
            digest=hashlib.sha256(temp.read_bytes()).hexdigest()
            with zipfile.ZipFile(destination/name,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.write(temp,'workspace.sqlite3')
                archive.writestr('manifest.json',js({'format':1,'created_at':now(),'sha256':digest}))
        finally: temp.unlink(missing_ok=True)
        return {'filename':name,'url':'/api/backups/'+name}

    def close(self): self.pool.shutdown(wait=True,cancel_futures=False)


def restore_backup(archive_path,data_dir):
    """Called only by CLI while holding the data-directory exclusive process lock."""
    data_dir=Path(data_dir);data_dir.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        if set(archive.namelist())!={'workspace.sqlite3','manifest.json'}: raise ValueError('备份格式无效')
        if archive.getinfo('workspace.sqlite3').file_size>1024**3: raise ValueError('备份超过1GB')
        blob=archive.read('workspace.sqlite3');meta=json.loads(archive.read('manifest.json'))
        if meta.get('format')!=1 or hashlib.sha256(blob).hexdigest()!=meta.get('sha256'): raise ValueError('备份校验失败')
    temp=data_dir/('restore-'+uuid.uuid4().hex+'.sqlite3');temp.write_bytes(blob)
    try:
        with closing(sqlite3.connect(temp)) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('备份数据库损坏')
            if con.execute('PRAGMA user_version').fetchone()[0]!=1: raise ValueError('不支持的数据库版本')
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'settings','tickets','documents','chunks','jobs','analyses','events'}<=tables: raise ValueError('备份缺少数据表')
        dst=data_dir/'workspace.sqlite3'
        if dst.exists():
            b=data_dir/'backups';b.mkdir(exist_ok=True)
            with closing(sqlite3.connect(dst)) as source,closing(sqlite3.connect(b/('before-restore-'+uuid.uuid4().hex+'.sqlite3'))) as target:
                source.backup(target)
            for suffix in ('-wal','-shm'): Path(str(dst)+suffix).unlink(missing_ok=True)
        os.replace(temp,dst)
    finally: temp.unlink(missing_ok=True)
    return dst
