"""로깅: stdout(= run_stock.sh 가 pipeline.log 로 리다이렉트) + 에러 메일 첨부용 메모리 버퍼."""
from __future__ import annotations

import logging
import sys
from collections import deque

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class BufferHandler(logging.Handler):
    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.lines: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def setup(verbose: bool = False) -> BufferHandler:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    buffer = BufferHandler()
    buffer.setFormatter(formatter)
    root.handlers[:] = [stream, buffer]
    # 외부 라이브러리의 과도한 로그 억제
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return buffer
