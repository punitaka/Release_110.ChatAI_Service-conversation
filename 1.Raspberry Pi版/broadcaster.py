"""
broadcaster.py
==============
会話イベントを複数のクライアントに配信するためのPub/Subクラス。2種類の受信方法に対応する。

  1. Server-Sent Events (SSE, /events) ── ブラウザ向け。長時間接続を維持してプッシュ配信する。
  2. ポーリング (/api/events?since=<seq>, app.py参照) ── CrowPanel(ESP-IDF)等、長時間接続の
     維持やチャンク解析が難しい組み込みクライアント向け。「前回のseq以降のイベント」を
     一定間隔でGETするだけで使える。history(直近500件)にseq番号を付けて保持している。

会話ループ(conversation_engine.run_conversation)はこのクラスの publish() を
呼ぶだけでよい。クライアントが1つも繋がっていなくても例外は発生せず、単に誰にも
届かないだけで会話ループ自体は止まらない(CrowPanel未接続時と同じ設計方針)。
"""

import queue
import threading
from collections import deque

HISTORY_SIZE = 500


class Broadcaster:
    def __init__(self):
        self._subscribers = []
        self._lock = threading.Lock()
        self._history = deque(maxlen=HISTORY_SIZE)  # [(seq, event), ...]
        self._next_seq = 1

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
            seq = self._next_seq
            self._next_seq += 1
            self._history.append((seq, event))
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

    def get_since(self, since_seq: int) -> list:
        """seq が since_seq より大きいイベントを [(seq, event), ...] で返す(ポーリング用)。
        historyの保持件数(HISTORY_SIZE)を超えて遡ることはできない。"""
        with self._lock:
            return [(seq, ev) for seq, ev in self._history if seq > since_seq]

    def latest_seq(self) -> int:
        """現時点までに発行された最新のseq番号(まだ何も無ければ0)。"""
        with self._lock:
            return self._next_seq - 1
