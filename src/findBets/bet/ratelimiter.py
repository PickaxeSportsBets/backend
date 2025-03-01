from datetime import datetime
import asyncio
from collections import deque
from datetime import datetime, timedelta


class RateLimiter:
    def __init__(self, calls_per_second: int = 25):
        self.calls_per_second = calls_per_second
        self.timestamps = deque(maxlen=calls_per_second)
        self.lock = asyncio.Lock()
        self.retry_delays = [1, 2, 4]  # Shorter delays for faster recovery
        self.current_retry = 0
        self.total_requests = 0
        self.last_reset = datetime.now()
        self.consecutive_429s = 0  # Track consecutive rate limit hits

    async def acquire(self):
        async with self.lock:
            now = datetime.now()

            # Clean up old timestamps
            while self.timestamps and (now - self.timestamps[0]) > timedelta(seconds=1):
                self.timestamps.popleft()

            # If we're at the limit, wait until we have room
            if len(self.timestamps) >= self.calls_per_second:
                wait_time = 1.0 - (now - self.timestamps[0]).total_seconds()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            self.timestamps.append(now)

    async def handle_rate_limit(self):
        self.consecutive_429s += 1
        if self.current_retry < len(self.retry_delays):
            delay = self.retry_delays[self.current_retry]
            self.current_retry += 1
            await asyncio.sleep(delay)
        else:
            await asyncio.sleep(self.retry_delays[-1])

        self.timestamps.clear()
        self.total_requests = max(0, self.total_requests - 10)

    def reset_retry_count(self):
        self.current_retry = 0
        self.consecutive_429s = 0  # Reset consecutive rate limits counter

