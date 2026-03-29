from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import ProcessingConfig, is_video_file, list_videos_recursive
from job_manager import JobManager


APP_ROOT = Path(__file__).resolve().parent
WEB_ROOT = APP_ROOT / "web"
STATE_DEFAULT = APP_ROOT / "state_data"
SETTINGS_FILE_NAME = "settings.json"


class ScanRequest(BaseModel):
    root_dir: str


class VideoSearchRequest(BaseModel):
    root_dir: str
    pattern: str = ""
    selected_folders: List[str] = Field(default_factory=list)
    limit: int = 200


class ProcessingConfigInput(BaseModel):
    algorithm: str = "MOG2"
    frame_rate: int = 1
    warmup_frames: int = 1
    resize_width: int = 640

    history: int = 15
    var_threshold: float = 16.0
    dist_threshold: float = 400.0
    stable_percent: float = 0.10
    motion_percent: float = 2.0

    diff_binary_threshold: int = 80
    diff_motion_percent: float = 0.06
    elapsed_frame_threshold: int = 85
    enable_frame_diff_refine: bool = False
    auto_detect_orientation: bool = False

    remove_duplicates: bool = True
    hash_func: str = "dhash"
    hash_size: int = 12
    similarity_threshold: int = 96
    hash_queue_len: int = 5

    keep_intermediate: bool = False

    def to_internal(self) -> ProcessingConfig:
        return ProcessingConfig(
            algorithm=self.algorithm,
            frame_rate=self.frame_rate,
            warmup_frames=self.warmup_frames,
            resize_width=self.resize_width,
            history=self.history,
            var_threshold=self.var_threshold,
            dist_threshold=self.dist_threshold,
            stable_percent=self.stable_percent,
            motion_percent=self.motion_percent,
            diff_binary_threshold=self.diff_binary_threshold,
            diff_motion_percent=self.diff_motion_percent,
            elapsed_frame_threshold=self.elapsed_frame_threshold,
            enable_frame_diff_refine=self.enable_frame_diff_refine,
            auto_detect_orientation=self.auto_detect_orientation,
            remove_duplicates=self.remove_duplicates,
            hash_func=self.hash_func,
            hash_size=self.hash_size,
            similarity_threshold=self.similarity_threshold,
            hash_queue_len=self.hash_queue_len,
            keep_intermediate=self.keep_intermediate,
        )


class UserSettings(BaseModel):
    config: ProcessingConfigInput = Field(default_factory=ProcessingConfigInput)
    last_root_dir: Optional[str] = None
    selected_folders: List[str] = Field(default_factory=list)
    search_pattern: str = ""


class SubmitJobRequest(BaseModel):
    root_dir: str
    selected_folders: List[str] = Field(default_factory=list)
    selected_files: List[str] = Field(default_factory=list)
    config: ProcessingConfigInput


class SaveSettingsRequest(BaseModel):
    root_dir: Optional[str] = None
    selected_folders: List[str] = Field(default_factory=list)
    search_pattern: str = ""
    config: ProcessingConfigInput


class ServerContext:
    def __init__(self, mapped_dir: str, state_dir: str):
        state_path = Path(state_dir).expanduser().resolve()
        state_path.mkdir(parents=True, exist_ok=True)

        self.mapped_dir = str(Path(mapped_dir).expanduser().resolve())
        self.state_dir = state_path
        self.settings_path = state_path / SETTINGS_FILE_NAME
        self.job_manager = JobManager(store_path=state_path / "jobs.json")

    def load_settings(self) -> UserSettings:
        if not self.settings_path.exists():
            return UserSettings()

        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return UserSettings.model_validate(data)
        except Exception:
            return UserSettings()

    def save_settings(self, settings: UserSettings) -> None:
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.settings_path)


context: Optional[ServerContext] = None


app = FastAPI(title="Video2PDF API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if WEB_ROOT.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")


@app.on_event("startup")
def on_startup() -> None:
    global context
    if context is None:
        context = ServerContext(
            mapped_dir=os.getenv("MAPPED_DIR", "/data"),
            state_dir=os.getenv("STATE_DIR", str(STATE_DEFAULT)),
        )


def normalize_root_dir(root_dir: str) -> Path:
    if not root_dir or not root_dir.strip():
        raise ValueError("目录不能为空")

    path = Path(root_dir).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"目录不存在: {path}")
    if not path.is_dir():
        raise ValueError(f"不是目录: {path}")

    return path


def to_relative(root_dir: Path, path: Path) -> str:
    rel = path.relative_to(root_dir)
    return rel.as_posix() if rel.parts else "."


def safe_resolve(root_dir: Path, relative_path: str) -> Path:
    if relative_path == ".":
        return root_dir

    path = (root_dir / relative_path).resolve()
    if path != root_dir and root_dir not in path.parents:
        raise ValueError(f"非法路径: {relative_path}")

    return path


def build_folder_video_map(root_dir: Path, videos: List[Path]) -> dict[str, set[Path]]:
    folder_map: dict[str, set[Path]] = {}
    for video in videos:
        current = video.parent
        while True:
            rel_dir = to_relative(root_dir, current)
            folder_map.setdefault(rel_dir, set()).add(video)
            if current == root_dir:
                break
            current = current.parent

    return folder_map


def collect_videos_from_folders(root_dir: Path, selected_folders: List[str]) -> set[Path]:
    all_videos = list_videos_recursive(root_dir)
    folder_map = build_folder_video_map(root_dir, all_videos)

    selected: set[Path] = set()
    for rel_folder in selected_folders:
        selected.update(folder_map.get(rel_folder, set()))

    return selected


def collect_selected_videos(
    root_dir: Path,
    selected_folders: List[str],
    selected_files: List[str],
) -> List[Path]:
    selected = collect_videos_from_folders(root_dir, selected_folders)

    for rel_file in selected_files:
        file_path = safe_resolve(root_dir, rel_file)
        if is_video_file(file_path):
            selected.add(file_path)

    return sorted(selected, key=lambda p: str(p).lower())


def compile_patterns(pattern: str) -> List[re.Pattern[str]]:
    raw = [item.strip() for item in re.split(r"[\n,;]+", pattern) if item.strip()]
    if not raw:
        return []

    compiled = []
    for item in raw:
        compiled.append(re.compile(item, flags=re.IGNORECASE))
    return compiled


def filter_videos(
    root_dir: Path,
    selected_folders: List[str],
    pattern: str,
    limit: int,
) -> tuple[List[str], int]:
    candidate_videos = collect_videos_from_folders(root_dir, selected_folders)
    if not selected_folders:
        candidate_videos = set(list_videos_recursive(root_dir))

    patterns = compile_patterns(pattern)
    if not patterns:
        return [], len(candidate_videos)

    result: List[str] = []
    for video in sorted(candidate_videos, key=lambda p: str(p).lower()):
        rel = to_relative(root_dir, video)
        text = rel.lower()
        if any(regex.search(text) for regex in patterns):
            result.append(rel)

        if len(result) >= limit:
            break

    return result, len(candidate_videos)


def format_job_summary(job: dict) -> dict:
    summary = job.get("summary", {})
    return {
        "job_id": str(job.get("job_id", "")),
        "status": str(job.get("status", "")),
        "stop_requested": bool(job.get("stop_requested", False)),
        "created_at": str(job.get("created_at", "")),
        "started_at": str(job.get("started_at", "")),
        "ended_at": str(job.get("ended_at", "")),
        "summary": {
            "total": int(summary.get("total", 0)),
            "pending": int(summary.get("pending", 0)),
            "running": int(summary.get("running", 0)),
            "ok": int(summary.get("ok", 0)),
            "failed": int(summary.get("failed", 0)),
        },
    }


def format_job_detail(job: dict, root_dir: Optional[str] = None) -> dict:
    result = format_job_summary(job)

    root: Optional[Path] = None
    if root_dir:
        try:
            root = Path(root_dir).expanduser().resolve()
        except Exception:
            root = None

    videos = []
    for item in job.get("videos", []):
        video_path = Path(item.get("path", ""))
        pdf_path = item.get("pdf_path", "")

        rel_video = str(video_path)
        rel_pdf = str(pdf_path) if pdf_path else ""

        if root is not None:
            try:
                rel_video = to_relative(root, video_path)
            except Exception:
                rel_video = str(video_path)

            if pdf_path:
                try:
                    rel_pdf = to_relative(root, Path(pdf_path))
                except Exception:
                    rel_pdf = str(pdf_path)

        videos.append(
            {
                "video": rel_video,
                "status": str(item.get("status", "")),
                "progress": float(item.get("progress", 0.0)),
                "slide_count": int(item.get("slide_count", 0)),
                "pdf": rel_pdf,
                "message": str(item.get("message", "")),
                "started_at": str(item.get("started_at", "")),
                "ended_at": str(item.get("ended_at", "")),
            }
        )

    result["videos"] = videos
    return result


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/defaults")
def defaults() -> dict:
    if context is None:
        raise HTTPException(status_code=500, detail="server context not initialized")

    settings = context.load_settings()
    root_dir = settings.last_root_dir or context.mapped_dir

    return {
        "mapped_dir": context.mapped_dir,
        "settings": settings.model_dump(),
        "effective_root_dir": root_dir,
    }


@app.post("/api/settings")
def save_settings(request: SaveSettingsRequest) -> dict:
    if context is None:
        raise HTTPException(status_code=500, detail="server context not initialized")

    settings = UserSettings(
        config=request.config,
        last_root_dir=request.root_dir,
        selected_folders=request.selected_folders,
        search_pattern=request.search_pattern,
    )
    context.save_settings(settings)
    return {"ok": True}


@app.post("/api/scan")
def scan(request: ScanRequest) -> dict:
    try:
        root = normalize_root_dir(request.root_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    videos = list_videos_recursive(root)
    folder_map = build_folder_video_map(root, videos)
    folders = sorted(folder_map.keys(), key=lambda item: (item.count("/"), item))

    return {
        "root_dir": str(root),
        "counts": {
            "folders": len(folders),
            "files": len(videos),
        },
        "folders": folders,
    }


@app.post("/api/videos/search")
def search_videos(request: VideoSearchRequest) -> dict:
    try:
        root = normalize_root_dir(request.root_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    limit = min(max(request.limit, 1), 1000)

    try:
        videos, candidate_count = filter_videos(
            root,
            request.selected_folders,
            request.pattern,
            limit,
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"正则表达式错误: {exc}")

    return {
        "root_dir": str(root),
        "pattern": request.pattern,
        "selected_folders": request.selected_folders,
        "candidate_count": candidate_count,
        "returned_count": len(videos),
        "limit": limit,
        "videos": videos,
    }


@app.post("/api/jobs")
def submit_job(request: SubmitJobRequest) -> dict:
    if context is None:
        raise HTTPException(status_code=500, detail="server context not initialized")

    try:
        root = normalize_root_dir(request.root_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        videos = collect_selected_videos(root, request.selected_folders, request.selected_files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not videos:
        raise HTTPException(status_code=400, detail="未选择任何可处理的视频")

    config = request.config.to_internal()
    try:
        # Fail fast to avoid creating obviously invalid jobs.
        from engine import validate_processing_config

        validate_processing_config(config)
        if config.algorithm.upper() == "FRAMEDIFF":
            config.enable_frame_diff_refine = False
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"参数不合法: {exc}")

    try:
        job_id = context.job_manager.submit(str(root), videos, config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"任务提交失败: {exc}")

    job = context.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=500, detail="任务提交后未找到")

    return {
        "message": f"任务已提交: {job_id}",
        "job": format_job_summary(job),
    }


@app.get("/api/jobs")
def list_jobs() -> dict:
    if context is None:
        raise HTTPException(status_code=500, detail="server context not initialized")
    jobs = context.job_manager.list_jobs()
    return {"jobs": [format_job_summary(job) for job in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, root_dir: Optional[str] = None) -> dict:
    if context is None:
        raise HTTPException(status_code=500, detail="server context not initialized")
    job = context.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"未找到任务: {job_id}")

    return {"job": format_job_detail(job, root_dir=root_dir)}


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict:
    if context is None:
        raise HTTPException(status_code=500, detail="server context not initialized")

    job = context.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"未找到任务: {job_id}")

    ok = context.job_manager.stop(job_id)
    if not ok:
        return {"ok": False, "message": f"任务不可停止: {job_id}"}
    return {"ok": True, "message": f"已请求停止任务: {job_id}"}


@app.get("/")
def index() -> FileResponse:
    index_file = WEB_ROOT / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="frontend not found")
    return FileResponse(index_file)


@app.get("/history")
def history_page() -> FileResponse:
    history_file = WEB_ROOT / "history.html"
    if not history_file.exists():
        raise HTTPException(status_code=404, detail="history page not found")
    return FileResponse(history_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Video2PDF FastAPI server")
    parser.add_argument("--mapped-dir", default="/data")
    parser.add_argument("--state-dir", default=str(STATE_DEFAULT))
    parser.add_argument("--server-name", default="0.0.0.0")
    parser.add_argument("--server-port", type=int, default=7860)
    return parser.parse_args()


def build_app_from_args(args: argparse.Namespace) -> FastAPI:
    global context
    context = ServerContext(mapped_dir=args.mapped_dir, state_dir=args.state_dir)
    return app


if __name__ == "__main__":
    import uvicorn

    args = parse_args()
    os.environ["MAPPED_DIR"] = args.mapped_dir
    os.environ["STATE_DIR"] = args.state_dir
    build_app_from_args(args)

    uvicorn.run("api_app:app", host=args.server_name, port=args.server_port, reload=False)
