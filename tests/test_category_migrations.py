import json
import tempfile
import unittest
from pathlib import Path
from app import load,ROOT
from workspace import Workspace,CATEGORIES

class CategoryTests(unittest.TestCase):
    def test_dataset_split_and_taxonomy(self):
        rows=json.loads((ROOT/'dataset.json').read_text())
        self.assertEqual(len(rows),436)
        self.assertEqual(len({r['text'] for r in rows}),len(rows))
        self.assertEqual({r['label'] for r in rows},set(CATEGORIES)-{'待人工分类'})
        for label in CATEGORIES[:-1]:
            self.assertGreaterEqual(sum(r['label']==label and r['split']=='train' for r in rows),40)
            self.assertGreaterEqual(sum(r['label']==label and r['split']=='test' for r in rows),12)

    def test_legacy_migration_is_backed_up_audited_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws=Workspace(tmp,load()[0])
            try:
                t=ws.create_ticket({'title':'历史工单','description':'旧客户端提交的描述','category':'网络与连接'})
                with ws.connect() as con:
                    con.execute("UPDATE tickets SET category='系统故障' WHERE id=?",(t['id'],))
                    con.execute('INSERT INTO analyses(ticket_id,ticket_version,result,created_at) VALUES (?,?,?,?)',
                                (t['id'],1,json.dumps({'sources':[],'category':'系统故障'}),'test'))
                ws.migrate_categories()
                after=ws.ticket(t['id'])
                self.assertEqual(after['category'],'待人工分类')
                self.assertEqual(after['version'],2)
                self.assertTrue(after['analysis']['stale'])
                self.assertTrue(any('系统故障' in e['body'] for e in after['events']))
                self.assertEqual(len(list((Path(tmp)/'backups').glob('*.zip'))),1)
                ws.migrate_categories()
                self.assertEqual(ws.ticket(t['id'])['version'],2)
                self.assertEqual(len(list((Path(tmp)/'backups').glob('*.zip'))),1)
            finally:ws.close()

    def test_legacy_input_and_unknown_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws=Workspace(tmp,load()[0])
            try:
                t=ws.create_ticket({'title':'历史账单','description':'需要发票','category':'支付账单'})
                self.assertEqual(t['category'],'支付与账单')
                t=ws.update_ticket(t['id'],{'version':t['version'],'category':'账号登录'})
                self.assertEqual(t['category'],'待人工分类')
                t=ws.create_ticket({'title':'🌊','description':'🌞'})
                self.assertEqual(t['category'],'待人工分类')
            finally:ws.close()
