from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Dict, Iterable, List, Optional

from engine import ProcessingConfig, ProcessingResult, cleanup_video_cache, process_video


@dataclass
class QueuedVideo:
    path: str


class JobStore:
    def __init__(self, store_path: Path):
        self.store_path = store_path
        self._lock = threading.Lock()
        self._jobs: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            self._jobs = {}
            return

        try:
            content = self.store_path.read_text(encoding="utf-8")
            if not content.strip():
                self._jobs = {}
                return
            data = json.loads(content)
            self._jobs = data if isinstance(data, dict) else {}
        except Exception:
            self._jobs = {}

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.store_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._jobs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.store_path)

    def create_job(self, root_dir: str, videos: Iterable[Path], config: ProcessingConfig) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = _utc_now()

        video_items = []
        for video in videos:
            video_items.append(
                {
                    "path": str(video),
                    "status": "pending",
                    "progress": 0.0,
                    "slide_count": 0,
                    "pdf_path": "",
                    "message": "pending",
                    "started_at": "",
                    "ended_at": "",
                }
            )

        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "root_dir": root_dir,
                "status": "queued",
                "stop_requested": False,
                "created_at": now,
                "started_at": "",
                "ended_at": "",
                "config": _serialize_config(config),
                "videos": video_items,
                "summary": {
                    "total": len(video_items),
                    "pending": len(video_items),
                    "running": 0,
                    "ok": 0,
                    "failed": 0,
                },
            }
            self._save()

        return job_id

    def mark_job_running(self, job_id: str) -> None:
        now = _utc_now()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "running"
            if not job["started_at"]:
                job["started_at"] = now
            self._save()

    def update_video_progress(
        self,
        job_id: str,
        video_index: int,
        progress: float,
        message: str,
        status: Optional[str] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            videos = job.get("videos", [])
            if video_index < 0 or video_index >= len(videos):
                return

            video_item = videos[video_index]
            video_item["progress"] = round(min(max(progress, 0.0), 1.0), 4)
            video_item["message"] = message
            if status is not None:
                video_item["status"] = status
            self._refresh_summary(job)
            self._save()

    def mark_video_started(self, job_id: str, video_index: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            videos = job.get("videos", [])
            if video_index < 0 or video_index >= len(videos):
                return

            video_item = videos[video_index]
            video_item["status"] = "running"
            video_item["started_at"] = _utc_now()
            video_item["message"] = "running"
            self._refresh_summary(job)
            self._save()

    def finalize_video(
        self,
        job_id: str,
        video_index: int,
        result: ProcessingResult,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            videos = job.get("videos", [])
            if video_index < 0 or video_index >= len(videos):
                return

            video_item = videos[video_index]
            video_item["status"] = result.status
            video_item["progress"] = 1.0 if result.status == "ok" else max(video_item.get("progress", 0.0), 0.01)
            video_item["slide_count"] = int(result.slide_count)
            video_item["pdf_path"] = str(result.pdf_path) if result.pdf_path is not None else ""
            video_item["message"] = result.message
            video_item["ended_at"] = _utc_now()
            self._refresh_summary(job)
            self._save()

    def finalize_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            summary = job.get("summary", {})
            if job.get("status") == "stopped":
                self._refresh_summary(job)
                self._save()
                return

            failed = int(summary.get("failed", 0))
            job["status"] = "done" if failed == 0 else "done_with_errors"
            job["ended_at"] = _utc_now()
            self._refresh_summary(job)
            self._save()

    def request_stop(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False

            if job.get("status") in {"done", "done_with_errors", "failed", "stopped"}:
                return False

            job["stop_requested"] = True
            self._save()
            return True

    def mark_job_stopped(self, job_id: str, message: str = "stopped by user") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            now = _utc_now()
            job["status"] = "stopped"
            job["stop_requested"] = True
            if not job.get("started_at"):
                job["started_at"] = now
            job["ended_at"] = now

            for item in job.get("videos", []):
                if item.get("status") in {"pending", "running"}:
                    item["status"] = "stopped"
                    item["message"] = message
                    if not item.get("started_at"):
                        item["started_at"] = now
                    item["ended_at"] = now

            self._refresh_summary(job)
            self._save()

    def fail_job(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            job["status"] = "failed"
            job["ended_at"] = _utc_now()
            for item in job.get("videos", []):
                if item.get("status") in {"pending", "running"}:
                    item["status"] = "failed"
                    item["message"] = message
                    item["ended_at"] = _utc_now()
            self._refresh_summary(job)
            self._save()

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return json.loads(json.dumps(job))

    def list_jobs(self) -> List[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
            jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            return json.loads(json.dumps(jobs))

    def resumable_job_ids(self) -> List[str]:
        with self._lock:
            ids = []
            for job_id, job in self._jobs.items():
                status = job.get("status")
                if status in {"queued", "running"}:
                    ids.append(job_id)
            return ids

    def _refresh_summary(self, job: dict) -> None:
        pending = 0
        running = 0
        ok = 0
        failed = 0

        for item in job.get("videos", []):
            status = item.get("status")
            if status == "pending":
                pending += 1
            elif status == "running":
                running += 1
            elif status == "ok":
                ok += 1
            elif status in {"failed", "error", "stopped"}:
                failed += 1

        total = len(job.get("videos", []))
        job["summary"] = {
            "total": total,
            "pending": pending,
            "running": running,
            "ok": ok,
            "failed": failed,
        }


class JobManager:
    def __init__(self, store_path: Path):
        self.store = JobStore(store_path=store_path)
        self.queue: Queue[str] = Queue()
        self._stop_events: Dict[str, threading.Event] = {}
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()
        self._requeue_resumable_jobs()

    def submit(self, root_dir: str, videos: Iterable[Path], config: ProcessingConfig) -> str:
        video_list = list(videos)
        if not video_list:
            raise ValueError("no videos to process")

        job_id = self.store.create_job(root_dir, video_list, config)
        self._stop_events[job_id] = threading.Event()
        self.queue.put(job_id)
        return job_id

    def list_jobs(self) -> List[dict]:
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> Optional[dict]:
        return self.store.get_job(job_id)

    def stop(self, job_id: str) -> bool:
        requested = self.store.request_stop(job_id)
        if not requested:
            return False

        self._stop_events.setdefault(job_id, threading.Event()).set()
        job = self.store.get_job(job_id)
        if job and job.get("status") == "queued":
            self.store.mark_job_stopped(job_id)
        return True

    def _requeue_resumable_jobs(self) -> None:
        for job_id in self.store.resumable_job_ids():
            self.queue.put(job_id)

    def _worker_loop(self) -> None:
        while True:
            try:
                job_id = self.queue.get(timeout=1)
            except Empty:
                continue

            try:
                self._run_job(job_id)
            finally:
                self.queue.task_done()

    def _run_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return

        status = str(job.get("status", ""))
        if status in {"done", "done_with_errors", "failed", "stopped"}:
            return

        stop_event = self._stop_events.setdefault(job_id, threading.Event())
        if bool(job.get("stop_requested")) or stop_event.is_set():
            self.store.mark_job_stopped(job_id)
            return

        try:
            self.store.mark_job_running(job_id)
            config = _deserialize_config(job.get("config", {}))
            videos = job.get("videos", [])

            for index, item in enumerate(videos):
                if stop_event.is_set():
                    self.store.mark_job_stopped(job_id)
                    break

                current_status = item.get("status")
                if current_status in {"ok", "stopped"}:
                    continue

                video_path = Path(item.get("path", ""))
                self.store.mark_video_started(job_id, index)

                def on_progress(progress: float, message: str, idx: int = index) -> None:
                    self.store.update_video_progress(
                        job_id,
                        idx,
                        progress=progress,
                        message=message,
                        status="running",
                    )

                result = process_video(
                    video_path,
                    config,
                    progress_callback=on_progress,
                    stop_callback=stop_event.is_set,
                )

                if result.status == "stopped":
                    cleanup_video_cache(video_path)
                self.store.finalize_video(job_id, index, result)

                if result.status == "stopped":
                    self.store.mark_job_stopped(job_id)
                    break

            self.store.finalize_job(job_id)
        except Exception as exc:
            self.store.fail_job(job_id, str(exc))
        finally:
            self._stop_events.pop(job_id, None)


def _serialize_config(config: ProcessingConfig) -> dict:
    return {
        "algorithm": config.algorithm,
        "frame_rate": config.frame_rate,
        "warmup_frames": config.warmup_frames,
        "resize_width": config.resize_width,
        "history": config.history,
        "var_threshold": config.var_threshold,
        "dist_threshold": config.dist_threshold,
        "stable_percent": config.stable_percent,
        "motion_percent": config.motion_percent,
        "diff_binary_threshold": config.diff_binary_threshold,
        "diff_motion_percent": config.diff_motion_percent,
        "elapsed_frame_threshold": config.elapsed_frame_threshold,
        "enable_frame_diff_refine": config.enable_frame_diff_refine,
        "auto_detect_orientation": config.auto_detect_orientation,
        "remove_duplicates": config.remove_duplicates,
        "hash_func": config.hash_func,
        "hash_size": config.hash_size,
        "similarity_threshold": config.similarity_threshold,
        "hash_queue_len": config.hash_queue_len,
        "keep_intermediate": config.keep_intermediate,
    }


def _deserialize_config(data: dict) -> ProcessingConfig:
    return ProcessingConfig(
        algorithm=data.get("algorithm", "MOG2"),
        frame_rate=int(data.get("frame_rate", 1)),
        warmup_frames=int(data.get("warmup_frames", 1)),
        resize_width=int(data.get("resize_width", 640)),
        history=int(data.get("history", 15)),
        var_threshold=float(data.get("var_threshold", 16.0)),
        dist_threshold=float(data.get("dist_threshold", 400.0)),
        stable_percent=float(data.get("stable_percent", 0.10)),
        motion_percent=float(data.get("motion_percent", 2.0)),
        diff_binary_threshold=int(data.get("diff_binary_threshold", 80)),
        diff_motion_percent=float(data.get("diff_motion_percent", 0.06)),
        elapsed_frame_threshold=int(data.get("elapsed_frame_threshold", 85)),
        enable_frame_diff_refine=bool(data.get("enable_frame_diff_refine", False)),
        auto_detect_orientation=bool(data.get("auto_detect_orientation", False)),
        remove_duplicates=bool(data.get("remove_duplicates", True)),
        hash_func=data.get("hash_func", "dhash"),
        hash_size=int(data.get("hash_size", 12)),
        similarity_threshold=int(data.get("similarity_threshold", 96)),
        hash_queue_len=int(data.get("hash_queue_len", 5)),
        keep_intermediate=bool(data.get("keep_intermediate", False)),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
