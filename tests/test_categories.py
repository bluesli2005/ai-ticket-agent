"""当前分类器分支的数据契约测试，不依赖 RAG 工作台模块。"""
import json
import unittest
from app import ROOT, load


class CategoryTests(unittest.TestCase):
    def test_taxonomy_and_split(self):
        rows = json.loads((ROOT / 'dataset.json').read_text())
        labels = {'账号与认证', '权限与访问', '支付与账单', '网络与连接',
                  '应用与数据故障', '功能与改进需求'}
        self.assertEqual({row['label'] for row in rows}, labels)
        train = {row['text'] for row in rows if row['split'] == 'train'}
        test = {row['text'] for row in rows if row['split'] == 'test'}
        self.assertEqual(len(train), 340)
        self.assertEqual(len(test), 96)
        self.assertFalse(train & test)
        self.assertEqual(len(train | test), len(rows))

    def test_model_exposes_six_scores_and_rejects_unknown(self):
        model, report = load()
        self.assertEqual(len(report['labels']), 6)
        self.assertEqual(len(model.predict('请开通查看权限')['ranking']), 6)
        self.assertTrue(model.predict('🌊🌞')['uncertain'])


if __name__ == '__main__':
    unittest.main()
