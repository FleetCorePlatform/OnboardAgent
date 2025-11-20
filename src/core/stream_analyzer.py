#!/usr/bin/env python

import gi
import numpy as np
import time
from ultralytics import YOLO

gi.require_version("Gst", "1.0")
from gi.repository import Gst


class StreamAnalyzer:
    def __init__(self, port: int, yolo_path: str, sample_rate: int):
        Gst.init(None)

        self.port = port
        self.yolo_path = yolo_path
        self.sample_rate = sample_rate

        self._frame = None
        self._video_pipe = None
        self._video_sink = None

    def run(self):
        command: str = (
            f"udpsrc port={self.port} ! application/x-rtp, payload=96 ! rtph264depay ! h264parse \
            ! avdec_h264 ! videoconvert ! video/x-raw,format=(string)BGR \
            ! appsink emit-signals=true sync=false max-buffers=2 drop=true"
        )

        self._video_pipe = Gst.parse_launch(command)
        self._video_pipe.set_state(Gst.State.PLAYING)
        self._video_sink = self._video_pipe.get_by_name("appsink0")

        self._video_sink.connect("new-sample", self._decode_callback)

    def _gst_to_opencv(self, sample) -> np.ndarray:
        buf = sample.get_buffer()
        caps = sample.get_caps()
        array = np.ndarray(
            (
                caps.get_structure(0).get_value("height"),
                caps.get_structure(0).get_value("width"),
                3,
            ),
            buffer=buf.extract_dup(0, buf.get_size()),
            dtype=np.uint8,
        )
        return array

    def _decode_callback(self, sink):
        sample = sink.emit("pull-sample")
        self._frame = self._gst_to_opencv(sample)

        return Gst.FlowReturn.OK

    def process_frame(self, frame, model):
        results = model(frame, verbose=False)

        for result in results:
            boxes = result.boxes
            for box in boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                class_name = model.names[cls_id]
                xyxy = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = map(int, xyxy)

                print(
                    f"Detected: {class_name} (class {cls_id}), confidence={conf:.2f}, bbox=[{x1},{y1},{x2},{y2}]"
                )

        return results


if __name__ == "__main__":
    print("Loading YOLO model...")
    model = YOLO("yolov8n.pt")
    print("Model loaded.")

    video = StreamAnalyzer()

    frame_skip = 15
    frame_count = 0
    last_process_time = 0
    min_interval = 0.2

    print("Stream processor started. Press Ctrl+C to stop.")

    try:
        while True:
            if not video.frame_available():
                time.sleep(0.001)
                continue

            frame = video.frame()
            frame_count += 1
            current_time = time.time()

            if frame_count % frame_skip != 0:
                continue

            if current_time - last_process_time < min_interval:
                continue

            process_frame(frame, model)
            last_process_time = current_time

    except KeyboardInterrupt:
        print("\nShutting down...")
        video._video_pipe.set_state(Gst.State.NULL)
