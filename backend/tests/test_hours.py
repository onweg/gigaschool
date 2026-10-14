"""Тесты солвера часов. Арифметика — главный критерий приёмки программы АЦР."""
from __future__ import annotations

import pytest

from app.core.hours import (
    align_to_grain,
    distribute_modules,
    rebalance_topics,
    split_by_kind,
    split_exact,
)


@pytest.mark.parametrize(
    ("total", "weights"),
    [
        (16, [1, 2, 2, 1]),
        (24, [3, 4, 2]),
        (36, [1, 1, 1, 1, 1]),
        (72, [3, 5, 4, 2, 1]),
        (248, [1]),
        (256, [1] * 6),
        (500, [7, 3, 5, 2, 9, 4]),
    ],
)
def test_split_exact_preserves_sum(total: float, weights: list[float]) -> None:
    parts = split_exact(total, weights)
    assert sum(parts) == pytest.approx(total)
    assert all(part >= 0 for part in parts)


def test_split_exact_zero_weights() -> None:
    assert split_exact(10, [0, 0]) == [0.0, 0.0]
    assert split_exact(0, [1, 1]) == [0.0, 0.0]


def test_split_by_kind_sum_matches_row() -> None:
    hours = split_by_kind(72, lectures=2, practice=3, labs=1, self_study=2)
    assert hours.total == pytest.approx(72)


def test_min_practice_share_enforced() -> None:
    """Запрос 1 кейса: практики не меньше половины всех часов."""
    hours = split_by_kind(16, lectures=3, practice=1, min_practice_share=0.5)
    assert hours.practice + hours.labs >= 8
    assert hours.total == pytest.approx(16)


def test_internship_all_self_study() -> None:
    """Запрос 5 кейса: 256 ч = 254 ч СРС + 2 ч аттестации, аудиторных нет."""
    totals = distribute_modules(256, 2, [1] * 6)
    assert sum(totals) == pytest.approx(254)
    rows = [split_by_kind(t, self_study=1) for t in totals]
    assert all(row.contact == 0 for row in rows)
    assert sum(row.self_study for row in rows) == pytest.approx(254)


def test_distribute_modules_rejects_oversized_attestation() -> None:
    with pytest.raises(ValueError):
        distribute_modules(16, 20, [1, 1])


def test_rebalance_topics_matches_module() -> None:
    topics = rebalance_topics(23, [2, 3, 1])
    assert sum(topics) == pytest.approx(23)


def test_align_to_grain() -> None:
    assert align_to_grain(3.5, 1.0) == 4.0
    assert align_to_grain(0.2, 1.0, minimum=1.0) == 1.0
    assert align_to_grain(2.4, 0.5) == 2.5
