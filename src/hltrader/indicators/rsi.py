from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RsiWarmupPolicy:
    period: int = 14
    warmup_bars: int = 30

    def __post_init__(self) -> None:
        if self.period <= 0 or self.warmup_bars < self.period:
            raise ValueError("warmup must contain at least one full RSI period")

    def ready(self, historical_bar_count: int, indicator_initialized: bool) -> bool:
        return historical_bar_count >= self.warmup_bars and indicator_initialized
