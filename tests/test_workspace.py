import base64
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from ai import AIUnavailable,Ollama,validate_endpoint
from app import load
from workspace import Workspace,Conflict,restore_backup

class FakeAI:
    offline=False
    def __init__(self,settings): pass
    def embed(self,texts):
        if self.offline:raise AIUnavailable('测试：模型不可用')
        return [[1.,float('VPN' in t),float('账号' in t)] for t in texts]
    def models(self):return ['qwen2.5:3b','qwen3-embedding:0.6b']
    def answer(self,title,body,sources):
        if self.offline:raise AIUnavailable('测试：模型不可用')
        return {'answerable':True,'summary':title,'missing_info':['客户端版本'],'steps':[{'text':'核对排查步骤','source_ids':[1]}],'reply':'请核对文档中的排查步骤。'}

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.ws=Workspace(self.path/'data',load()[0],FakeAI);FakeAI.offline=False
    def tearDown(self):self.ws.close();self.tmp.cleanup();FakeAI.offline=False
    def wait(self,jid,fail=False):
        for _ in range(500):
            j=self.ws.job(jid)
            if j['status']=='failed':
                if fail:return j
                self.fail(j['error'])
            if j['status']=='done':return j['result']
            time.sleep(.01)
        self.fail('job timeout')
    def doc(self,text='VPN 认证超时，请检查设备时间并记录错误提示。',**kw):
        d=self.ws.save_document({'title':'VPN 指南','content':text,**kw})
        if d['job_id']:self.wait(d['job_id'])
        return d['id']
    def ticket(self):return self.ws.create_ticket({'title':'VPN 认证超时','description':'VPN 无法连接，提示认证超时。'})
    def analysis(self,t):
        self.wait(self.ws.analyse(t['id'])['job_id']);return self.ws.ticket(t['id'])['analysis']

    def test_persistence_status_conflict_and_reopen(self):
        t=self.ticket()
        with self.assertRaises(ValueError):self.ws.update_ticket(t['id'],{'version':1,'status':'已解决'})
        with self.assertRaises(ValueError):self.ws.update_ticket(t['id'],{'version':1,'status':'已关闭','resolution':'done'})
        t=self.ws.update_ticket(t['id'],{'version':1,'status':'已解决','resolution':'校准设备时间后恢复'})
        with self.assertRaises(Conflict):self.ws.update_ticket(t['id'],{'version':1,'status':'待处理'})
        t=self.ws.update_ticket(t['id'],{'version':t['version'],'status':'已关闭'})
        other=Workspace(self.path/'data',load()[0],FakeAI)
        try:self.assertEqual(other.ticket(t['id'])['status'],'已关闭')
        finally:other.close()
        t=self.ws.update_ticket(t['id'],{'version':t['version'],'status':'待处理'})
        self.assertEqual(len(t['events']),4)

    def test_document_update_and_delete_remove_old_evidence(self):
        did=self.doc();old=self.ws.document(did)
        self.assertTrue(self.ws.search('VPN 认证超时')['sources'])
        r=self.ws.save_document({'title':'打印指南','content':'打印设备卡纸，请联系管理员。','revision':old['revision']},did)
        self.assertFalse(any(s['revision']==old['revision'] for s in self.ws.search('VPN 认证超时')['sources']))
        self.wait(r['job_id']);self.ws.delete_document(did)
        self.assertEqual(self.ws.search('打印设备卡纸')['sources'],[])

    def test_duplicate_import_rejected(self):
        self.doc()
        with self.assertRaises(Conflict):self.doc()

    def test_solution_not_retrieved_until_approved(self):
        t=self.ticket()
        with self.assertRaises(ValueError):self.ws.solution(t['id'])
        self.ws.update_ticket(t['id'],{'version':1,'status':'已解决','resolution':'VPN 设备时间校准后恢复正常'})
        d=self.ws.solution(t['id']);self.assertFalse(self.ws.document(d['id'])['approved'])
        self.assertEqual(self.ws.search('VPN 设备时间')['sources'],[])
        self.wait(self.ws.reindex(d['id'],approve=True)['job_id'])
        self.assertTrue(self.ws.search('VPN 设备时间')['sources'])

    def test_model_outage_preserves_manual_and_lexical_paths(self):
        FakeAI.offline=True;did=self.doc();self.assertEqual(self.ws.document(did)['status'],'lexical')
        self.assertTrue(self.ws.search('VPN 认证超时')['sources'])
        a=self.analysis(self.ticket());self.assertEqual(a['result']['mode'],'extractive')
        self.assertFalse(a['result']['answerable'])

    def test_unrelated_query_has_no_answer(self):
        self.doc();self.assertEqual(self.ws.search('明天天气预报下雨吗')['sources'],[])
        t=self.ws.create_ticket({'title':'明天天气预报','description':'明天会下雨吗？'})
        self.assertEqual(self.analysis(t)['result']['mode'],'no_evidence')

    def test_deleted_citation_blocks_acceptance(self):
        did=self.doc();t=self.ticket();a=self.analysis(t);self.assertFalse(a['stale'])
        self.ws.delete_document(did);self.assertTrue(self.ws.ticket(t['id'])['analysis']['stale'])
        with self.assertRaises(Conflict):self.ws.feedback(t['id'],{'analysis_id':a['id'],'feedback':'accepted'})

    def test_edit_invalidates_analysis(self):
        self.doc();t=self.ticket();self.analysis(t)
        self.ws.update_ticket(t['id'],{'version':1,'description':'所有设备均无法访问'})
        self.assertTrue(self.ws.ticket(t['id'])['analysis']['stale'])

    def test_model_change_invalidates_vectors(self):
        did=self.doc();s=self.ws.settings();s['embed_model']='new-local-model'
        self.assertTrue(self.ws.update_settings(s)['reindex_required'])
        self.assertEqual(self.ws.document(did)['status'],'lexical')
        self.assertEqual(self.ws.search('VPN 认证超时')['mode'],'lexical')

    def test_backup_restores_files_vectors_and_history(self):
        did=self.doc();t=self.ticket();self.analysis(t)
        b=self.ws.backup();target=self.path/'restored';restore_backup(self.ws.root/'backups'/b['filename'],target)
        other=Workspace(target,load()[0],FakeAI)
        try:
            self.assertEqual(other.ticket(t['id'])['title'],t['title'])
            self.assertEqual(other.document_file(did),self.ws.document_file(did))
            self.assertEqual(other.search('VPN 认证超时')['mode'],'hybrid')
            self.assertTrue(other.ticket(t['id'])['analysis']['result']['sources'])
        finally:other.close()

    def test_invalid_backup_rejected(self):
        p=self.path/'bad.zip'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('workspace.sqlite3',b'not-a-database');z.writestr('manifest.json',json.dumps({'format':1,'sha256':'bad'}))
        with self.assertRaises(ValueError):restore_backup(p,self.path/'restore')

    def test_cloud_endpoints_and_models_rejected(self):
        for url in ['https://api.example.com','http://127.0.0.1:11434@evil.com','http://127.0.0.1:11434/path','http://[::1]:-1']:
            with self.assertRaises(ValueError):validate_endpoint(url)
        s=self.ws.settings();s['chat_model']='some-cloud-model'
        with self.assertRaises(ValueError):self.ws.update_settings(s)

    def test_invalid_model_citations_rejected(self):
        client=Ollama(self.ws.settings())
        payload={'message':{'content':json.dumps({'selected_passage_ids':[99],'missing_info':[]})}}
        with patch.object(client,'request',return_value=payload):
            with self.assertRaises(AIUnavailable):client.answer('a','b',[{'source_id':1,'title':'VPN guide','text':'VPN connection timeout: check the device time and authentication prompt.'}])

    def test_invalid_encoding_has_visible_failure(self):
        d=self.ws.save_document({'title':'损坏文本','filename':'bad.txt','file_base64':base64.b64encode(b'\xff\xfe\x00').decode()})
        self.wait(d['job_id'],fail=True)
        doc=self.ws.document(d['id']);self.assertEqual(doc['status'],'failed');self.assertIn('UTF-8',doc['error']);self.assertEqual(doc['chunks'],[])

    def test_pdf_text_extraction_preserves_page(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(500,500)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 50 400 Td (VPN authentication timeout: check device time.) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream);out=io.BytesIO();writer.write(out)
        d=self.ws.save_document({'title':'PDF guide','filename':'guide.pdf','file_base64':base64.b64encode(out.getvalue()).decode()})
        self.wait(d['job_id']);doc=self.ws.document(d['id'])
        self.assertEqual(doc['chunks'][0]['page'],1);self.assertIn('authentication timeout',doc['content'])

    def test_deleted_during_query_cannot_be_returned(self):
        did=self.doc();original=FakeAI.embed
        def during(client,texts):self.ws.delete_document(did);return original(client,texts)
        with patch.object(FakeAI,'embed',during):self.assertEqual(self.ws.search('VPN 认证超时')['sources'],[])

    def test_grounded_answer_uses_original_paragraph_and_title(self):
        client=Ollama(self.ws.settings())
        source={'source_id':1,'title':'VPN guide','text':'## 条件\n如果普通网页可访问，请先校准设备时间，再重新完成认证。'}
        data={'message':{'content':json.dumps({'selected_passage_ids':[1],'missing_info':['错误提示']})}}
        with patch.object(client,'request',return_value=data):
            result=client.answer('尚未尝试任何操作','VPN 超时',[source])
        self.assertEqual(result['summary'],'尚未尝试任何操作')
        self.assertEqual(result['steps'][0]['text'],'如果普通网页可访问，请先校准设备时间，再重新完成认证。')
        self.assertIn(result['steps'][0]['text'],source['text'])
        self.assertNotIn('##',result['reply'])

    def test_invalid_save_does_not_record_acceptance(self):
        self.doc();t=self.ticket();a=self.analysis(t)
        with self.assertRaises(ValueError):
            self.ws.update_ticket(t['id'],{'version':1,'status':'已解决','analysis_id':a['id'],'reply':a['result']['reply']})
        self.assertIsNone(self.ws.ticket(t['id'])['analysis']['feedback'])

    def test_stale_analysis_cannot_be_saved_with_reply(self):
        did=self.doc();t=self.ticket();a=self.analysis(t);self.ws.delete_document(did)
        with self.assertRaises(Conflict):
            self.ws.update_ticket(t['id'],{'version':1,'analysis_id':a['id'],'reply':a['result']['reply']})
        self.assertEqual(self.ws.ticket(t['id'])['reply'],'')

    def test_feedback_recorded(self):
        self.doc();t=self.ticket();a=self.analysis(t)
        self.ws.feedback(t['id'],{'analysis_id':a['id'],'feedback':'edited'})
        self.assertEqual(self.ws.ticket(t['id'])['analysis']['feedback'],'edited')

if __name__=='__main__':unittest.main()
