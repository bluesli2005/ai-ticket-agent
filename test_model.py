import unittest
from model import Classifier, evaluate
from app import load


class Tests(unittest.TestCase):
    def test_learns_from_labels(self):
        a = [{'text': '密码登录', 'label': 'A'}, {'text': '支付退款', 'label': 'B'}]
        b = [{'text': r['text'], 'label': 'B' if r['label']=='A' else 'A'} for r in a]
        self.assertEqual(Classifier(a).predict('密码登录')['candidate'], 'A')
        self.assertEqual(Classifier(b).predict('密码登录')['candidate'], 'B')

    def test_unknown_is_rejected(self):
        model, _ = load()
        for text in ['🌊🌞', 'zyxwv987654321', '']:
            self.assertTrue(model.predict(text)['uncertain'])

    def test_scores_are_normalized(self):
        model, _ = load()
        result = model.predict('退款支付' * 100)
        self.assertAlmostEqual(sum(x['score'] for x in result['ranking']), 1)

    def test_evaluation_counts_rejection_as_error(self):
        model = Classifier([{'text': '密码登录', 'label': 'A'}, {'text': '支付退款', 'label': 'B'}])
        report = evaluate(model, [{'text': 'xyz', 'label': 'A'}])
        self.assertEqual(report['accuracy'], 0)
        self.assertEqual(report['matrix']['A']['需要人工判断'], 1)


if __name__ == '__main__':
    unittest.main()
