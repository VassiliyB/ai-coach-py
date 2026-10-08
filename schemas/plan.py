# schemas/plan.py
"""Схемы структурированных планов. Модель возвращает данные, темпы и текст формирует код."""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkoutType(str, Enum):
    REST = "rest"              # полный отдых
    EASY = "easy"              # лёгкий кросс (зона E)
    LONG = "long"              # длительный бег (зона E)
    MARATHON = "marathon"      # марафонский темп (зона M)
    THRESHOLD = "threshold"    # пороговая работа (зона T)
    INTERVAL = "interval"      # интервалы МПК (зона I)
    REPETITION = "repetition"  # повторы (зона R)
    CROSS = "cross"            # ОФП / растяжка / кросс-тренинг


RUNNING_TYPES = frozenset({
    WorkoutType.EASY, WorkoutType.LONG, WorkoutType.MARATHON,
    WorkoutType.THRESHOLD, WorkoutType.INTERVAL, WorkoutType.REPETITION,
})
QUALITY_TYPES = frozenset({
    WorkoutType.MARATHON, WorkoutType.THRESHOLD, WorkoutType.INTERVAL, WorkoutType.REPETITION,
})
ZONE_BY_TYPE = {
    WorkoutType.EASY: "E",
    WorkoutType.LONG: "E",
    WorkoutType.MARATHON: "M",
    WorkoutType.THRESHOLD: "T",
    WorkoutType.INTERVAL: "I",
    WorkoutType.REPETITION: "R",
}


class PlannedDay(BaseModel):
    """Один день недели. day: 1 = понедельник ... 7 = воскресенье."""
    model_config = ConfigDict(extra="ignore")

    day: int = Field(ge=1, le=7)
    type: WorkoutType
    distance_km: Optional[float] = Field(default=None, ge=0, le=60)   # общая дистанция, с разминкой
    quality_km: Optional[float] = Field(default=None, ge=0, le=30)    # только "рабочая" часть качественной
    description: str = Field(default="", max_length=300)              # например "5 × 1000 м, отдых 2 мин"

    @model_validator(mode="after")
    def _check_consistency(self) -> "PlannedDay":
        if self.type in RUNNING_TYPES:
            if not self.distance_km:
                raise ValueError(f"для тренировки '{self.type.value}' нужна distance_km > 0")
        elif self.distance_km:
            raise ValueError(f"для '{self.type.value}' distance_km должна быть пустой")

        if self.type in QUALITY_TYPES:
            if not self.quality_km:
                raise ValueError(f"для '{self.type.value}' нужна quality_km > 0 (рабочая часть)")
            if self.quality_km > (self.distance_km or 0):
                raise ValueError("quality_km не может быть больше distance_km")
        elif self.quality_km:
            raise ValueError("quality_km указывается только для качественных тренировок")
        return self

    @property
    def zone(self) -> Optional[str]:
        """Зона Дэниелса, выведенная кодом из типа тренировки."""
        return ZONE_BY_TYPE.get(self.type)


class WeekPlan(BaseModel):
    """Недельный микроцикл: ровно 7 дней."""
    model_config = ConfigDict(extra="ignore")

    days: List[PlannedDay] = Field(min_length=7, max_length=7)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _days_are_1_to_7(self) -> "WeekPlan":
        if sorted(d.day for d in self.days) != list(range(1, 8)):
            raise ValueError("дни должны быть ровно 1..7 (Пн..Вс), без пропусков и повторов")
        self.days.sort(key=lambda d: d.day)
        return self

    @property
    def total_km(self) -> float:
        return round(sum(d.distance_km or 0.0 for d in self.days), 1)


# Названия фаз по Дэниелсу. Их задаёт код: модель называла фазы как попало ("Фаза 1", "Тaper").
PHASE_NAMES = {
    1: "Закладка фундамента",
    2: "Раннее качество",
    3: "Переходное качество",
    4: "Финальная подводка",
}


class Phase(BaseModel):
    """Фаза периодизации Дэниелса. weeks = 0 допустимо для коротких планов (фаза пропускается)."""
    model_config = ConfigDict(extra="ignore")

    number: int = Field(ge=1, le=4)
    name: str = Field(default="", max_length=100)   # всегда перезаписывается из PHASE_NAMES
    weeks: int = Field(ge=0, le=52)
    focus: str = Field(default="", max_length=400)


class MacroPlan(BaseModel):
    """Макроцикл: 4 фазы и целевой километраж по каждой неделе."""
    model_config = ConfigDict(extra="ignore")

    phases: List[Phase] = Field(min_length=4, max_length=4)
    weekly_km: List[float] = Field(min_length=2, max_length=60)
    notes: List[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _check_structure(self) -> "MacroPlan":
        if [p.number for p in self.phases] != [1, 2, 3, 4]:
            raise ValueError("фазы должны идти по порядку: 1, 2, 3, 4")
        for phase in self.phases:
            phase.name = PHASE_NAMES[phase.number]
        if any(km <= 0 or km > 250 for km in self.weekly_km):
            raise ValueError("недельный километраж должен быть в диапазоне (0; 250] км")
        if len(self.weekly_km) != self.total_weeks:
            raise ValueError(
                f"в weekly_km {len(self.weekly_km)} значений, а сумма недель по фазам {self.total_weeks}"
            )
        return self

    @property
    def total_weeks(self) -> int:
        return sum(p.weeks for p in self.phases)

    def week_phase_numbers(self) -> List[int]:
        """Номер фазы для каждой недели плана по порядку (фазы с weeks = 0 пропускаются)."""
        return [p.number for p in self.phases for _ in range(p.weeks)]

    def phase_for_week(self, week_number: int) -> Phase:
        """Фаза, в которую попадает неделя подготовки (нумерация с 1)."""
        acc = 0
        for phase in self.phases:
            acc += phase.weeks
            if week_number <= acc:
                return phase
        return self.phases[-1]