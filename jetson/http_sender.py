"""Background HTTP sender for non-blocking POSTs from real-time loops."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Literal

import requests


@dataclass(slots=True)
class _PostRequest:
    kind: Literal["json", "form"]
    url: str
    timeout: float
    json_body: dict[str, Any] | None = None
    form_data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None


class HttpSender:
    """Queue-backed HTTP sender with a single worker thread.

    Intended for control/vision contexts where request latency must never block
    control-rate loops. If the queue is full, oldest work is dropped so newest
    state and captures win.
    """

    def __init__(self, max_queue: int = 64) -> None:
        self._q: queue.Queue[_PostRequest] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def close(self, timeout_s: float = 1.0) -> None:
        """Request worker shutdown and wait briefly."""
        self._stop.set()
        self._thread.join(timeout=timeout_s)

    def post_json(self, url: str, json_body: dict[str, Any], timeout: float = 0.5) -> None:
        self._enqueue(
            _PostRequest(
                kind="json",
                url=url,
                timeout=timeout,
                json_body=json_body,
            )
        )

    def post_form(
        self,
        url: str,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, str],
        timeout: float = 2.0,
    ) -> None:
        self._enqueue(
            _PostRequest(
                kind="form",
                url=url,
                timeout=timeout,
                files=files,
                form_data=data,
            )
        )

    def _enqueue(self, req: _PostRequest) -> None:
        try:
            self._q.put_nowait(req)
            return
        except queue.Full:
            pass

        try:
            self._q.get_nowait()
        except queue.Empty:
            pass

        try:
            self._q.put_nowait(req)
        except queue.Full:
            pass

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                req = self._q.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                if req.kind == "json":
                    requests.post(req.url, json=req.json_body, timeout=req.timeout)
                else:
                    requests.post(req.url, files=req.files, data=req.form_data, timeout=req.timeout)
            except Exception:
                # Intentionally swallow to keep sender resilient.
                pass
