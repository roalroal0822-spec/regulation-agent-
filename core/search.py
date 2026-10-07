"""BM25 검색. 외부 검색엔진 없이 문서 블록·조문·절차를 찾는다."""
import math
from collections import Counter

from .textutil import tokens


class BM25:
    def __init__(self, items, text_of, k1=1.4, b=0.75):
        """items: 검색 대상 목록, text_of: item -> 검색용 문자열"""
        self.items = list(items)
        self.docs = [Counter(tokens(text_of(it))) for it in self.items]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0
        self.df = Counter()
        for d in self.docs:
            self.df.update(d.keys())
        self.k1, self.b = k1, b
        self.N = len(self.docs)

    def idf(self, t):
        n = self.df.get(t, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def search(self, query, k=8, min_score=0.0):
        q = tokens(query)
        if not q or not self.N:
            return []
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            L = self.lens[i] or 1
            for t in q:
                f = d.get(t)
                if not f:
                    continue
                s += self.idf(t) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / (self.avg or 1)))
            if s > min_score:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [(self.items[i], round(s, 3)) for s, i in scores[:k]]
