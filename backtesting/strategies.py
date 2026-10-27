
from dataclasses import dataclass



from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    name: str
    partial_fraction: float = 0.0
    partial_points: float = 5.0
    breakeven_trigger: float | None = None


STRATEGIES = [
    Strategy(
        name="original_tp1",
        partial_fraction=0.0,
    ),
    Strategy(
        name="partial_50",
        partial_fraction=0.50,
        partial_points=5.0,
    ),
    Strategy(
        name="partial_75",
        partial_fraction=0.75,
        partial_points=5.0,
    ),
    Strategy(
        name="fixed_5",
        partial_fraction=1.0,
        partial_points=5.0,
    ),
    Strategy(
        name="fixed_5_be_3",
        partial_fraction=1.0,
        partial_points=5.0,
        breakeven_trigger=3.0,
    ),
]
