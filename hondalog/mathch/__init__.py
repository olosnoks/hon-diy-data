"""Math channels: values worked out from other channels, live and on replay.

    from hondalog.mathch import MathChannel, MathEngine

    engine = MathEngine([MathChannel("duty", "[inj] * [rpm] / 1200", units="%")],
                        inputs=["inj", "rpm"])
    engine.update({"inj": 2.4, "rpm": 3000.0}, t=12.5)      # {'duty': 6.0}

Expressions are compiled once (see ``hondalog.expr``). A math channel may use
another math channel; the engine sorts them so inputs come first and refuses
cycles. A channel whose inputs are missing reads ``None`` for that sample;
nothing in here raises while polling.
"""

from .engine import MathChannel, MathEngine, MathError

__all__ = ["MathChannel", "MathEngine", "MathError"]
