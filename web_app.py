from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set

import gradio as gr

from engine import ProcessingConfig, is_video_file, list_videos_recursive
from job_manager import JobManager


DEFAULT_HEADERS = ["Video", "Status", "Progress", "Slides", "PDF", "Message"]
JOBS_HEADERS = ["Job ID", "Status", "Total", "Pending", "Running", "OK", "Failed", "Created At"]
APP_ROOT = Path(__file__).resolve().parent
STYLE_PATH = APP_ROOT / "web_style.css"
DEFAULT_STATE_DIR = APP_ROOT / "state"


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


def build_folder_video_map(root_dir: Path, videos: Sequence[Path]) -> Dict[str, Set[Path]]:
    folder_map: Dict[str, Set[Path]] = {}

    for video in videos:
        current = video.parent
        while True:
            rel_dir = to_relative(root_dir, current)
            folder_map.setdefault(rel_dir, set()).add(video)
            if current == root_dir:
                break
            current = current.parent

    return folder_map


def collect_selected_videos(
    root_dir: Path,
    selected_folders: Sequence[str],
    selected_files: Sequence[str],
) -> List[Path]:
    all_videos = list_videos_recursive(root_dir)
    folder_map = build_folder_video_map(root_dir, all_videos)

    selected: Set[Path] = set()

    for rel_file in selected_files:
        file_path = safe_resolve(root_dir, rel_file)
        if is_video_file(file_path):
            selected.add(file_path)

    for rel_folder in selected_folders:
        selected.update(folder_map.get(rel_folder, set()))

    return sorted(selected, key=lambda path: str(path).lower())


def build_config(
    algorithm: str,
    frame_rate: float,
    warmup_frames: float,
    resize_width: float,
    history: float,
    var_threshold: float,
    dist_threshold: float,
    stable_percent: float,
    motion_percent: float,
    diff_binary_threshold: float,
    diff_motion_percent: float,
    elapsed_frame_threshold: float,
    remove_duplicates: bool,
    hash_func: str,
    hash_size: float,
    similarity_threshold: float,
    hash_queue_len: float,
    keep_intermediate: bool,
) -> ProcessingConfig:
    return ProcessingConfig(
        algorithm=algorithm,
        frame_rate=int(frame_rate),
        warmup_frames=int(warmup_frames),
        resize_width=int(resize_width),
        history=int(history),
        var_threshold=float(var_threshold),
        dist_threshold=float(dist_threshold),
        stable_percent=float(stable_percent),
        motion_percent=float(motion_percent),
        diff_binary_threshold=int(diff_binary_threshold),
        diff_motion_percent=float(diff_motion_percent),
        elapsed_frame_threshold=int(elapsed_frame_threshold),
        remove_duplicates=bool(remove_duplicates),
        hash_func=hash_func,
        hash_size=int(hash_size),
        similarity_threshold=int(similarity_threshold),
        hash_queue_len=int(hash_queue_len),
        keep_intermediate=bool(keep_intermediate),
    )


def scan_directory(root_dir: str):
    try:
        root = normalize_root_dir(root_dir)
    except ValueError as exc:
        empty_update = gr.update(choices=[], value=[])
        return empty_update, empty_update, f"扫描失败: {exc}"

    videos = list_videos_recursive(root)
    folder_map = build_folder_video_map(root, videos)

    folder_choices = sorted(folder_map.keys(), key=lambda item: (item.count("/"), item))
    video_choices = [to_relative(root, video) for video in videos]

    status = (
        f"已扫描 `{root}`。"
        f"共发现 **{len(video_choices)}** 个视频，**{len(folder_choices)}** 个可选文件夹。"
    )

    return (
        gr.update(choices=folder_choices, value=[]),
        gr.update(choices=video_choices, value=[]),
        status,
    )


def format_jobs_table(jobs: Sequence[dict]) -> List[List[str]]:
    rows: List[List[str]] = []
    for job in jobs:
        summary = job.get("summary", {})
        rows.append(
            [
                str(job.get("job_id", "")),
                str(job.get("status", "")),
                str(summary.get("total", 0)),
                str(summary.get("pending", 0)),
                str(summary.get("running", 0)),
                str(summary.get("ok", 0)),
                str(summary.get("failed", 0)),
                str(job.get("created_at", "")),
            ]
        )
    return rows


def format_job_details(root_dir: str, job: dict) -> List[List[str]]:
    root = Path(root_dir).expanduser().resolve()
    rows: List[List[str]] = []

    for item in job.get("videos", []):
        video_path = Path(item.get("path", ""))
        pdf_path = item.get("pdf_path", "")

        try:
            rel_video = to_relative(root, video_path)
        except Exception:
            rel_video = str(video_path)

        if pdf_path:
            pdf_abs = Path(pdf_path)
            try:
                rel_pdf = to_relative(root, pdf_abs)
            except Exception:
                rel_pdf = str(pdf_abs)
        else:
            rel_pdf = ""

        rows.append(
            [
                rel_video,
                str(item.get("status", "")),
                f"{float(item.get('progress', 0.0)) * 100:.1f}%",
                str(item.get("slide_count", 0)),
                rel_pdf,
                str(item.get("message", "")),
            ]
        )

    return rows


def submit_job(
    job_manager: JobManager,
    root_dir: str,
    selected_folders: Sequence[str],
    selected_files: Sequence[str],
    algorithm: str,
    frame_rate: float,
    warmup_frames: float,
    resize_width: float,
    history: float,
    var_threshold: float,
    dist_threshold: float,
    stable_percent: float,
    motion_percent: float,
    diff_binary_threshold: float,
    diff_motion_percent: float,
    elapsed_frame_threshold: float,
    remove_duplicates: bool,
    hash_func: str,
    hash_size: float,
    similarity_threshold: float,
    hash_queue_len: float,
    keep_intermediate: bool,
):
    try:
        root = normalize_root_dir(root_dir)
        videos = collect_selected_videos(root, selected_folders, selected_files)
        if not videos:
            jobs = format_jobs_table(job_manager.list_jobs())
            return "未选择任何可处理的视频。", jobs

        config = build_config(
            algorithm,
            frame_rate,
            warmup_frames,
            resize_width,
            history,
            var_threshold,
            dist_threshold,
            stable_percent,
            motion_percent,
            diff_binary_threshold,
            diff_motion_percent,
            elapsed_frame_threshold,
            remove_duplicates,
            hash_func,
            hash_size,
            similarity_threshold,
            hash_queue_len,
            keep_intermediate,
        )

        job_id = job_manager.submit(str(root), videos, config)
        jobs = format_jobs_table(job_manager.list_jobs())
        msg = f"任务已提交：`{job_id}`，共 {len(videos)} 个视频。可以关闭浏览器，任务会在后台继续执行。"
        return msg, jobs
    except Exception as exc:
        jobs = format_jobs_table(job_manager.list_jobs())
        return f"任务提交失败: {exc}", jobs


def refresh_jobs(job_manager: JobManager):
    jobs = job_manager.list_jobs()
    return format_jobs_table(jobs)


def refresh_job_detail(job_manager: JobManager, root_dir: str, job_id: str):
    if not job_id or not job_id.strip():
        return "请输入 Job ID", []

    job = job_manager.get_job(job_id.strip())
    if job is None:
        return f"未找到 Job ID: {job_id}", []

    summary = job.get("summary", {})
    status_text = (
        f"Job `{job_id}` 状态: **{job.get('status', '')}**。"
        f"总计 {summary.get('total', 0)}，运行中 {summary.get('running', 0)}，"
        f"成功 {summary.get('ok', 0)}，失败 {summary.get('failed', 0)}。"
    )
    rows = format_job_details(root_dir, job)
    return status_text, rows


def load_css() -> str:
    if STYLE_PATH.exists():
        return STYLE_PATH.read_text(encoding="utf-8")
    return ""


def build_demo(default_root_dir: str, job_manager: JobManager) -> gr.Blocks:
    css = load_css()

    with gr.Blocks(css=css, title="Video2PDF Batch") as demo:
        gr.Markdown(
            """
            # Video2PDF 批量处理器
            任务提交后由后台 Worker 持续执行。关闭网页后任务不会中断，重新打开可查看进度。
            """,
            elem_id="hero-title",
        )

        with gr.Row(elem_classes=["panel"]):
            root_dir = gr.Textbox(
                label="目录映射地址",
                value=default_root_dir,
                placeholder="例如: /data/videos",
            )
            scan_button = gr.Button("扫描目录", variant="primary")

        scan_status = gr.Markdown("点击“扫描目录”后选择文件或文件夹。", elem_classes=["hint"])

        with gr.Row(elem_classes=["pickers"]):
            folder_picker = gr.CheckboxGroup(
                choices=[],
                label="选择文件夹（递归包含该目录下所有视频）",
            )
            file_picker = gr.CheckboxGroup(
                choices=[],
                label="选择单个视频文件",
            )

        with gr.Accordion("阈值与算法参数", open=False):
            algorithm = gr.Dropdown(
                choices=["MOG2", "KNN", "FrameDiff"],
                value="MOG2",
                label="抽帧算法",
            )

            with gr.Row():
                frame_rate = gr.Slider(1, 6, value=1, step=1, label="处理帧间隔 (N)")
                warmup_frames = gr.Slider(0, 50, value=1, step=1, label="Warmup 帧数")
                resize_width = gr.Slider(320, 1600, value=640, step=10, label="缩放宽度")

            with gr.Row():
                history = gr.Slider(5, 300, value=15, step=1, label="背景模型历史长度")
                var_threshold = gr.Slider(1, 200, value=16, step=1, label="MOG2 varThreshold")
                dist_threshold = gr.Slider(10, 2000, value=400, step=10, label="KNN distThreshold")

            with gr.Row():
                stable_percent = gr.Slider(0.01, 10, value=0.10, step=0.01, label="静止阈值 (%)")
                motion_percent = gr.Slider(0.1, 30, value=2.0, step=0.1, label="运动阈值 (%)")

            with gr.Row():
                diff_binary_threshold = gr.Slider(
                    1,
                    255,
                    value=80,
                    step=1,
                    label="FrameDiff 二值阈值",
                )
                diff_motion_percent = gr.Slider(
                    0.01,
                    15,
                    value=0.06,
                    step=0.01,
                    label="FrameDiff 运动阈值 (%)",
                )
                elapsed_frame_threshold = gr.Slider(
                    1,
                    300,
                    value=85,
                    step=1,
                    label="FrameDiff 延迟抓取帧数",
                )

            with gr.Row():
                remove_duplicates = gr.Checkbox(value=True, label="自动去重")
                keep_intermediate = gr.Checkbox(value=False, label="保留中间截图")

            with gr.Row():
                hash_func = gr.Dropdown(
                    choices=["dhash", "phash", "ahash"],
                    value="dhash",
                    label="去重哈希算法",
                )
                hash_size = gr.Slider(8, 24, value=12, step=1, label="哈希尺寸")
                similarity_threshold = gr.Slider(
                    85,
                    100,
                    value=96,
                    step=1,
                    label="相似度阈值 (%)",
                )
                hash_queue_len = gr.Slider(1, 30, value=5, step=1, label="去重历史窗口")

        submit_button = gr.Button("提交后台任务", variant="primary", size="lg")
        submit_status = gr.Markdown("", elem_classes=["summary"])

        with gr.Accordion("任务列表", open=True):
            with gr.Row():
                refresh_jobs_button = gr.Button("刷新任务列表")
            jobs_table = gr.Dataframe(
                headers=JOBS_HEADERS,
                datatype=["str"] * len(JOBS_HEADERS),
                row_count=(0, "dynamic"),
                col_count=(len(JOBS_HEADERS), "fixed"),
                label="后台任务",
                interactive=False,
            )

        with gr.Accordion("任务详情", open=True):
            with gr.Row():
                job_id_input = gr.Textbox(label="Job ID", placeholder="粘贴任务 ID")
                refresh_detail_button = gr.Button("查看详情")

            detail_status = gr.Markdown("输入 Job ID 后查看进度。")
            detail_table = gr.Dataframe(
                headers=DEFAULT_HEADERS,
                datatype=["str"] * len(DEFAULT_HEADERS),
                row_count=(0, "dynamic"),
                col_count=(len(DEFAULT_HEADERS), "fixed"),
                label="视频处理详情",
                interactive=False,
            )

        scan_button.click(
            fn=scan_directory,
            inputs=[root_dir],
            outputs=[folder_picker, file_picker, scan_status],
        )

        demo.load(
            fn=scan_directory,
            inputs=[root_dir],
            outputs=[folder_picker, file_picker, scan_status],
        )

        submit_button.click(
            fn=lambda *args: submit_job(job_manager, *args),
            inputs=[
                root_dir,
                folder_picker,
                file_picker,
                algorithm,
                frame_rate,
                warmup_frames,
                resize_width,
                history,
                var_threshold,
                dist_threshold,
                stable_percent,
                motion_percent,
                diff_binary_threshold,
                diff_motion_percent,
                elapsed_frame_threshold,
                remove_duplicates,
                hash_func,
                hash_size,
                similarity_threshold,
                hash_queue_len,
                keep_intermediate,
            ],
            outputs=[submit_status, jobs_table],
        )

        refresh_jobs_button.click(
            fn=lambda: refresh_jobs(job_manager),
            inputs=[],
            outputs=[jobs_table],
        )

        refresh_detail_button.click(
            fn=lambda root, job_id: refresh_job_detail(job_manager, root, job_id),
            inputs=[root_dir, job_id_input],
            outputs=[detail_status, detail_table],
        )

    return demo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Video2PDF web batch app")
    parser.add_argument(
        "--mapped-dir",
        default="/data",
        help="Default directory mapped in web UI",
    )
    parser.add_argument("--server-name", default="0.0.0.0", help="Server host")
    parser.add_argument("--server-port", type=int, default=7860, help="Server port")
    parser.add_argument("--share", action="store_true", help="Enable Gradio public sharing")
    parser.add_argument(
        "--state-dir",
        default=str(DEFAULT_STATE_DIR),
        help="Directory to persist job states",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    store_path = state_dir / "jobs.json"

    job_manager = JobManager(store_path=store_path)
    demo = build_demo(args.mapped_dir, job_manager)
    demo.queue().launch(
        server_name=args.server_name,
        server_port=args.server_port,
        share=args.share,
    )


if __name__ == "__main__":
    main()
