# We cannot get real LTE in SITL, so we just simulate it for now

import asyncio
import random
from typing import AsyncGenerator


async def get_signal_strength() -> AsyncGenerator[int, None]:
    simulated_rsrp = -80

    while True:
        await asyncio.sleep(0.15)

        delta = random.randint(-2, 2)
        simulated_rsrp += delta
        simulated_rsrp = max(-115, min(-60, simulated_rsrp))

        yield simulated_rsrp
