"""Детерминированное распределение часов.

Почему не LLM: в чек-листах АЦР арифметика часов — главный источник отказов
(в образце стажировки итоговая строка не сходится с модулями). Модель задаёт
только пропорции и ограничения, точные числа считает этот модуль.

Инвариант, который гарантируется конструктивно:
    sum(topic.hours) == module.hours  для каждого вида работы
    sum(module.hours.total) + final_attestation.hours == meta.total_hours
"""
from __future__ import annotations

from app.schemas.ir import HOUR_STEP, Hours


def _to_steps(value: float, grain: float = HOUR_STEP) -> int:
    return int(round(value / grain))


def _from_steps(steps: int, grain: float = HOUR_STEP) -> float:
    return round(steps * grain, 1)


def pick_grain(total: float) -> float:
    """Зерно округления. Длинные программы считают целыми часами, короткие —
    могут дробить до 0.5 ч (как в образце интенсива на 24 ч)."""
    if total >= 60 and float(total).is_integer():
        return 1.0
    return HOUR_STEP


def split_exact(
    total: float, weights: list[float], grain: float | None = None
) -> list[float]:
    """Разложить `total` по весам так, чтобы сумма совпала ровно.

    Метод наибольших остатков в единицах `grain`. Нулевые веса получают 0.
    """
    if not weights:
        return []
    grain = grain or pick_grain(total)
    total_steps = _to_steps(total, grain)
    weight_sum = sum(weights)
    if weight_sum <= 0 or total_steps <= 0:
        return [0.0] * len(weights)

    raw = [total_steps * w / weight_sum for w in weights]
    floors = [int(x) for x in raw]
    remainder = total_steps - sum(floors)
    # Доли остатка — по убыванию дробной части, при равенстве — по большему весу.
    order = sorted(
        range(len(weights)),
        key=lambda i: (raw[i] - floors[i], weights[i]),
        reverse=True,
    )
    for i in order[:remainder]:
        floors[i] += 1
    return [_from_steps(s, grain) for s in floors]


def split_by_kind(
    total: float,
    *,
    lectures: float = 0.0,
    practice: float = 0.0,
    labs: float = 0.0,
    self_study: float = 0.0,
    min_practice_share: float | None = None,
    grain: float | None = None,
) -> Hours:
    """Разложить часы строки по видам работы с точным сохранением суммы.

    Аргументы — относительные веса. `min_practice_share` поднимает долю
    практики (ПР+ЛБ) до требуемой, забирая часы у лекций, затем у СРС.
    """
    weights = [lectures, practice, labs, self_study]
    if sum(weights) <= 0:
        weights = [0.0, 0.0, 0.0, 1.0]  # полностью самостоятельная работа
    grain = grain or pick_grain(total)
    lk, pr, lb, srs = split_exact(total, weights, grain)
    hours = Hours(lectures=lk, practice=pr, labs=lb, self_study=srs)

    if min_practice_share is not None and total > 0:
        need_steps = _to_steps(total * min_practice_share, grain)
        have_steps = _to_steps(hours.practice + hours.labs, grain)
        deficit = need_steps - have_steps
        if deficit > 0:
            for donor in ("lectures", "self_study"):
                if deficit <= 0:
                    break
                available = _to_steps(getattr(hours, donor), grain)
                take = min(available, deficit)
                setattr(hours, donor, _from_steps(available - take, grain))
                hours.practice = _from_steps(
                    _to_steps(hours.practice, grain) + take, grain
                )
                deficit -= take
    return hours


def align_to_grain(value: float, grain: float, *, minimum: float | None = None) -> float:
    """Привести часы к кратности зерна — иначе остаток «потеряется» при делении."""
    aligned = _from_steps(max(1, _to_steps(value, grain)), grain)
    if minimum is not None and aligned < minimum:
        aligned = _from_steps(_to_steps(minimum, grain), grain)
    return aligned


def distribute_modules(
    total_program_hours: float,
    final_attestation_hours: float,
    module_weights: list[float],
    grain: float | None = None,
) -> list[float]:
    """Разложить объём программы по модулям за вычетом итоговой аттестации."""
    body = round(total_program_hours - final_attestation_hours, 1)
    if body < 0:
        raise ValueError(
            f"Итоговая аттестация ({final_attestation_hours} ч) больше объёма "
            f"программы ({total_program_hours} ч)"
        )
    return split_exact(body, module_weights, grain)


def rebalance_topics(
    module_total: float, topic_weights: list[float], grain: float | None = None
) -> list[float]:
    """Часы тем внутри модуля; сумма ровно равна модулю."""
    return split_exact(module_total, topic_weights, grain)
