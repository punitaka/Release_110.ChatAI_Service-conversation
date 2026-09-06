"""
broadcaster.py
==============
Server-Sent Events (SSE) で、複数のブラウザタブへ同時にイベントを配信するための
シンプルなPub/Subクラス。

会話ループ(conversation_engine.run_conversation)はこのクラスの publish() を
呼ぶだけでよい。ブラウザが1つも繋がっていなくても例外は発生せず、単に誰にも
届かないだけで会話ループ自体は止まらない(CrowPanel未接続時と同じ設計方針)。
"""

import queue
import threading


class Broadcaster:
    def __init__(self):
        self._subscribers = []
        self._lock = threading.Lock()

    def subscribe(self) -> "queue.Queue":
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue"):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict):
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # 受信が遅いクライアントのために会話ループを止めない。
                # 一番古いイベントを1件捨てて、最新イベントを優先する。
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except queue.Empty:
                    pass
