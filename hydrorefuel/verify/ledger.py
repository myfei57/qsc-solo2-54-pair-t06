"""校验历史台账。

台账保存两类记录：``run``（一次校验运行，含结论或部分失败原因）与
``migration``（判定版本迁移）。记录既留内存副本也按段落盘，重启后从落盘
流水回放，计量点的合格状态跨重启连续。每条运行记录内嵌当时的判定快照，
历史结论按当时那一版解释，台账从不重判旧记录。

台账与审计是同一份事实的两个出口：平台每写一条台账记录，同时在事件总线
上发一条对应事件（``verify_pass`` / ``verify_fail`` / ``verify_inconclusive``
/ ``verify_migrated``），审计日志按同一 ``run_id`` 落盘，三处可以互查。
"""

from __future__ import annotations

import threading

from ..store.journal import JsonlJournal


class VerifyLedger:
    """校验记录：内存副本 + 分段落盘。"""

    def __init__(self, journal: JsonlJournal, memory_limit: int = 4096) -> None:
        self._journal = journal
        self._memory_limit = max(1, memory_limit)
        self._lock = threading.RLock()
        self._records: list[dict] = [dict(record) for record in journal.read_all()][-self._memory_limit :]

    @property
    def journal(self) -> JsonlJournal:
        return self._journal

    def _append(self, record: dict) -> dict:
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._memory_limit:
                del self._records[: len(self._records) - self._memory_limit]
            self._journal.append(record)
        return record

    def append_run(self, record: dict) -> dict:
        """记一条校验运行记录（含未出结论的部分失败）。"""

        return self._append(record)

    def append_migration(self, record: dict) -> dict:
        """记一条判定版本迁移记录。"""

        return self._append(record)

    def history(self, point: str | None = None, limit: int = 50) -> list[dict]:
        """返回校验历史（最新的在前）；迁移记录属于全站，不按计量点过滤。"""

        with self._lock:
            records = list(self._records)
        selected: list[dict] = []
        for record in reversed(records):
            if point is not None and record.get("kind") == "run" and record.get("point") != point:
                continue
            selected.append(record)
            if len(selected) >= max(1, limit):
                break
        return selected

    def latest_conclusive(self, point: str) -> dict | None:
        """返回某计量点最近一条出了结论的运行记录；没有则返回 ``None``。"""

        with self._lock:
            records = list(self._records)
        for record in reversed(records):
            if record.get("kind") == "run" and record.get("point") == point and record.get("conclusive"):
                return record
        return None

    def status(self, point: str, on_fail: str) -> dict:
        """汇总某计量点的校验状态，供控制台与加注门禁读取。"""

        with self._lock:
            runs = [
                record
                for record in self._records
                if record.get("kind") == "run" and record.get("point") == point
            ]
        standing = None
        for record in reversed(runs):
            if record.get("conclusive"):
                standing = record
                break
        if standing is None:
            qualification = "unverified"
        elif standing.get("conclusion") == "pass":
            qualification = "qualified"
        else:
            qualification = "unqualified"
        return {
            "point": point,
            "on_fail": on_fail,
            "qualification": qualification,
            "runs": len(runs),
            "standing": standing,
            "last_run": runs[-1] if runs else None,
        }
