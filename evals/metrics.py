"""Entity span metrics (docs/evals/README.md): a prediction matches an expected span when the
type is equal and the overlap is at least 50 % of the longer of the two spans."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

MATCH_THRESHOLD = 0.5


@dataclass(frozen=True)
class Span:
    type: str
    start: int
    end: int


def overlap_ratio(a: Span, b: Span) -> float:
    inter = max(0, min(a.end, b.end) - max(a.start, b.start))
    longest = max(a.end - a.start, b.end - b.start)
    return inter / longest if longest else 0.0


@dataclass
class CaseMatch:
    tp: list[tuple[Span, Span]] = field(default_factory=list)
    fn: list[Span] = field(default_factory=list)
    fp: list[Span] = field(default_factory=list)
    confused: list[tuple[Span, Span]] = field(default_factory=list)  # (expected, predicted)


def match_case(expected: Iterable[Span], predicted: Iterable[Span]) -> CaseMatch:
    """Greedy one-to-one matching, best overlap first."""

    expected, predicted = list(expected), list(predicted)
    result = CaseMatch()
    used: set[int] = set()
    for exp in expected:
        best, best_ratio = None, MATCH_THRESHOLD
        for i, pred in enumerate(predicted):
            if i in used or pred.type != exp.type:
                continue
            ratio = overlap_ratio(exp, pred)
            if ratio >= best_ratio:
                best, best_ratio = i, ratio
        if best is None:
            result.fn.append(exp)
        else:
            used.add(best)
            result.tp.append((exp, predicted[best]))
    result.fp = [pred for i, pred in enumerate(predicted) if i not in used]
    for exp in result.fn:  # an overlapping prediction of another type: type confusion
        for pred in result.fp:
            if overlap_ratio(exp, pred) >= MATCH_THRESHOLD:
                result.confused.append((exp, pred))
    return result


@dataclass
class TypeScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / self.support if self.support else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


@dataclass
class Scores:
    by_type: dict[str, TypeScore] = field(default_factory=lambda: defaultdict(TypeScore))
    by_tag: dict[str, TypeScore] = field(default_factory=lambda: defaultdict(TypeScore))
    negatives: int = 0
    negatives_with_hits: int = 0
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)

    def add(self, tags: list[str], match: CaseMatch, *, negative: bool) -> None:
        for exp, _ in match.tp:
            self.by_type[exp.type].tp += 1
            for tag in tags:
                self.by_tag[tag].tp += 1
        for exp in match.fn:
            self.by_type[exp.type].fn += 1
            for tag in tags:
                self.by_tag[tag].fn += 1
        for pred in match.fp:
            self.by_type[pred.type].fp += 1
            for tag in tags:
                self.by_tag[tag].fp += 1
        for exp, pred in match.confused:
            self.confusion[(exp.type, pred.type)] += 1
        if negative:
            self.negatives += 1
            self.negatives_with_hits += bool(match.fp)

    def micro(self, types: Iterable[str] | None = None) -> TypeScore:
        total = TypeScore()
        for name, score in self.by_type.items():
            if types is None or name in types:
                total.tp += score.tp
                total.fp += score.fp
                total.fn += score.fn
        return total

    @property
    def false_alarm_rate(self) -> float | None:
        return self.negatives_with_hits / self.negatives if self.negatives else None
