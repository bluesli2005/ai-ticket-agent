"""可阅读的多项式朴素贝叶斯：字符二元组 → 计数 → 对数概率。"""
import math
import re
from collections import Counter


def features(text):
    parts = re.findall(r"[\w]+", text.lower())
    return Counter(pair for part in parts for pair in
                   ([part] if len(part) == 1 else [part[i:i+2] for i in range(len(part)-1)]))


class Classifier:
    def __init__(self, rows):
        self.docs = Counter()
        self.counts = {}
        self.vocabulary = set()
        for row in rows:
            label = row['label']
            tokens = features(row['text'])
            self.docs[label] += 1
            self.counts.setdefault(label, Counter()).update(tokens)
            self.vocabulary.update(tokens)
        if len(self.docs) < 2 or not self.vocabulary:
            raise ValueError('至少需要两个类别和有效训练文本')
        self.total = sum(self.docs.values())
        self.denominators = {k: sum(v.values()) + len(self.vocabulary)
                             for k, v in self.counts.items()}

    def predict(self, text):
        tokens = features(text)
        known = {t: n for t, n in tokens.items() if t in self.vocabulary}
        scores = {}
        for label, counts in self.counts.items():
            scores[label] = math.log(self.docs[label] / self.total) + sum(
                n * math.log((counts[t]+1) / self.denominators[label])
                for t, n in known.items())
        peak = max(scores.values())
        weights = {k: math.exp(v-peak) for k, v in scores.items()}
        norm = sum(weights.values())
        ranking = sorted([{'label': k, 'score': v/norm} for k, v in weights.items()],
                         key=lambda item: item['score'], reverse=True)
        coverage = sum(known.values()) / max(1, sum(tokens.values()))
        # 教学用启发式拒识，不是经过校准的安全保证。
        uncertain = coverage < .15 or ranking[0]['score'] < .65
        first, second = ranking[0]['label'], ranking[1]['label']
        contributions = sorted([
            {'token': t, 'weight': n * (
                math.log((self.counts[first][t]+1)/self.denominators[first]) -
                math.log((self.counts[second][t]+1)/self.denominators[second]))}
            for t, n in known.items()], key=lambda item: item['weight'], reverse=True)
        return {'label': '需要人工判断' if uncertain else first,
                'candidate': first, 'uncertain': uncertain, 'ranking': ranking,
                'coverage': coverage,
                'features': [x for x in contributions if x['weight'] > 0][:6]}


def evaluate(model, rows):
    labels = sorted(model.docs)
    matrix = {a: {b: 0 for b in labels + ['需要人工判断']} for a in labels}
    details = []
    for row in rows:
        pred = model.predict(row['text'])['label']
        matrix[row['label']][pred] += 1
        details.append({**row, 'prediction': pred, 'correct': pred == row['label']})
    metrics = []
    for label in labels:
        tp = matrix[label][label]
        precision = tp / max(1, sum(matrix[a][label] for a in labels))
        recall = tp / max(1, sum(matrix[label].values()))
        f1 = 2*precision*recall / (precision+recall) if precision+recall else 0
        metrics.append({'label': label, 'precision': precision, 'recall': recall, 'f1': f1})
    return {'accuracy': sum(x['correct'] for x in details)/max(1, len(details)),
            'macro_f1': sum(x['f1'] for x in metrics)/len(metrics),
            'test_count': len(rows), 'matrix': matrix, 'metrics': metrics, 'details': details}
