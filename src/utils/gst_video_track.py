import asyncio
import time
from fractions import Fraction

import av
import numpy as np
from aiortc import VideoStreamTrack

from loguru import logger


class GstVideoTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()
        self._queue = asyncio.Queue(maxsize=1)
        self._running = True
        self._start_time = None

    async def recv(self):
        if not self._running:
            raise Exception("Track is stopped")

        frame = await self._queue.get()

        if self._start_time is None:
            self._start_time = time.time()
            pts = 0
        else:
            pts = int((time.time() - self._start_time) * 90000)

        frame.pts = pts
        frame.time_base = Fraction(1, 90000)
        return frame

    def update_frame(self, frame_np: np.ndarray):
        if not self._running:
            return

        try:
            video_frame = av.VideoFrame.from_ndarray(frame_np, format="bgr24")

            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            self._queue.put_nowait(video_frame)
        except Exception as e:
            logger.error(f"Error updating frame in track: {e}")

    def stop(self):
        self._running = False
        if self._queue.empty():
            try:
                dummy = np.zeros((480, 640, 3), dtype=np.uint8)
                self.update_frame(dummy)
            except:
                pass
