from __future__ import annotations

import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import cv2
import numpy as np
from PIL import Image


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".flv",
    ".webm",
    ".m4v",
    ".wmv",
}

HASH_FUNC_SET = {"dhash", "phash", "ahash"}


@dataclass
class ProcessingConfig:
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

    remove_duplicates: bool = True
    hash_func: str = "dhash"
    hash_size: int = 12
    similarity_threshold: int = 96
    hash_queue_len: int = 5

    keep_intermediate: bool = False


@dataclass
class ProcessingResult:
    video_path: Path
    pdf_path: Optional[Path]
    slide_count: int
    status: str
    message: str


ProgressCallback = Callable[[float, str], None]
StopCallback = Callable[[], bool]


class ProcessingCancelled(Exception):
    pass


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def list_videos_recursive(directory: Path) -> List[Path]:
    return sorted(
        [path for path in directory.rglob("*") if path.is_file() and is_video_file(path)],
        key=lambda path: str(path).lower(),
    )


def validate_processing_config(config: ProcessingConfig) -> None:
    if config.frame_rate <= 0:
        raise ValueError("frame_rate must be > 0")
    if config.resize_width < 200:
        raise ValueError("resize_width must be >= 200")
    if config.warmup_frames < 0:
        raise ValueError("warmup_frames must be >= 0")
    if config.motion_percent <= config.stable_percent:
        raise ValueError("motion_percent must be greater than stable_percent")
    if config.algorithm.upper() not in {"MOG2", "KNN", "FRAMEDIFF"}:
        raise ValueError("algorithm must be one of: MOG2, KNN, FrameDiff")
    if config.diff_motion_percent < 0:
        raise ValueError("diff_motion_percent must be >= 0")
    if config.hash_func.lower() not in HASH_FUNC_SET:
        raise ValueError("hash_func must be one of: dhash, phash, ahash")
    if config.hash_queue_len <= 0:
        raise ValueError("hash_queue_len must be > 0")
    if not 1 <= config.similarity_threshold <= 100:
        raise ValueError("similarity_threshold must be in [1, 100]")
    if config.hash_size <= 1:
        raise ValueError("hash_size must be > 1")
    if config.elapsed_frame_threshold <= 0:
        raise ValueError("elapsed_frame_threshold must be > 0")


def process_video(
    video_path: Path,
    config: ProcessingConfig,
    progress_callback: Optional[ProgressCallback] = None,
    stop_callback: Optional[StopCallback] = None,
) -> ProcessingResult:
    validate_processing_config(config)

    video_path = video_path.expanduser().resolve()
    _check_cancel(stop_callback)
    _emit_progress(progress_callback, 0.0, "starting")

    if not is_video_file(video_path):
        return ProcessingResult(
            video_path=video_path,
            pdf_path=None,
            slide_count=0,
            status="failed",
            message="not a supported video file",
        )

    output_pdf_dir = video_path.parent / "pdf"
    output_pdf_dir.mkdir(parents=True, exist_ok=True)
    output_pdf_path = output_pdf_dir / f"{video_path.stem}.pdf"

    temp_dir_manager: Optional[tempfile.TemporaryDirectory[str]] = None
    image_dir: Path

    if config.keep_intermediate:
        image_dir = output_pdf_dir / f"{video_path.stem}_images"
        if image_dir.exists():
            for file in image_dir.glob("*"):
                if file.is_file():
                    file.unlink()
        else:
            image_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir_manager = tempfile.TemporaryDirectory(
            prefix=f".{video_path.stem}_", dir=str(output_pdf_dir)
        )
        image_dir = Path(temp_dir_manager.name)

    try:
        if config.algorithm.upper() == "FRAMEDIFF":
            capture_count = _extract_by_frame_diff(
                video_path,
                image_dir,
                config,
                progress_callback=progress_callback,
                stop_callback=stop_callback,
            )
        else:
            capture_count = _extract_by_bg_modeling(
                video_path,
                image_dir,
                config,
                progress_callback=progress_callback,
                stop_callback=stop_callback,
            )

        _check_cancel(stop_callback)
        _emit_progress(progress_callback, 0.88, "extract_finished")

        if capture_count == 0:
            return ProcessingResult(
                video_path=video_path,
                pdf_path=None,
                slide_count=0,
                status="failed",
                message="no slide-like frame captured",
            )

        if config.remove_duplicates:
            _check_cancel(stop_callback)
            _remove_duplicate_images(image_dir, config)
        _check_cancel(stop_callback)
        _emit_progress(progress_callback, 0.95, "deduplicate_finished")

        images = _list_images(image_dir)
        if not images:
            return ProcessingResult(
                video_path=video_path,
                pdf_path=None,
                slide_count=0,
                status="failed",
                message="all images removed during deduplication",
            )

        _convert_images_to_pdf(images, output_pdf_path)
        _emit_progress(progress_callback, 1.0, "done")
        return ProcessingResult(
            video_path=video_path,
            pdf_path=output_pdf_path,
            slide_count=len(images),
            status="ok",
            message="completed",
        )
    except ProcessingCancelled:
        return ProcessingResult(
            video_path=video_path,
            pdf_path=None,
            slide_count=0,
            status="stopped",
            message="stopped by user",
        )
    except Exception as exc:
        return ProcessingResult(
            video_path=video_path,
            pdf_path=None,
            slide_count=0,
            status="failed",
            message=str(exc),
        )
    finally:
        if temp_dir_manager is not None:
            temp_dir_manager.cleanup()


def _extract_by_bg_modeling(
    video_path: Path,
    image_dir: Path,
    config: ProcessingConfig,
    progress_callback: Optional[ProgressCallback] = None,
    stop_callback: Optional[StopCallback] = None,
) -> int:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"unable to open video: {video_path}")

    if config.algorithm.upper() == "MOG2":
        subtractor = cv2.createBackgroundSubtractorMOG2(
            history=config.history,
            varThreshold=config.var_threshold,
            detectShadows=False,
        )
    elif config.algorithm.upper() == "KNN":
        subtractor = cv2.createBackgroundSubtractorKNN(
            history=config.history,
            dist2Threshold=config.dist_threshold,
            detectShadows=False,
        )
    else:
        raise ValueError("background modeling only supports MOG2/KNN")

    frame_no = 0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    capture_enabled = False
    screenshot_count = 0
    use_frame_diff_refine = bool(config.enable_frame_diff_refine)
    prev_gray = None
    diff_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)) if use_frame_diff_refine else None

    try:
        while cap.isOpened():
            _check_cancel(stop_callback)
            ret, frame = cap.read()
            if not ret:
                break

            frame_no += 1
            _emit_frame_progress(progress_callback, frame_no, total_frames)
            if frame_no % config.frame_rate != 0:
                continue

            resized = _resize_keep_ratio(frame, config.resize_width)
            mask = subtractor.apply(resized)

            diff_motion_percent = 0.0
            if use_frame_diff_refine:
                gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
                if prev_gray is not None:
                    frame_diff = cv2.absdiff(gray, prev_gray)
                    _, frame_diff = cv2.threshold(
                        frame_diff,
                        config.diff_binary_threshold,
                        255,
                        cv2.THRESH_BINARY,
                    )
                    frame_diff = cv2.dilate(frame_diff, diff_kernel)
                    diff_motion_percent = (cv2.countNonZero(frame_diff) / float(frame_diff.size)) * 100.0
                prev_gray = gray

            if frame_no <= config.warmup_frames:
                continue

            foreground_percent = (cv2.countNonZero(mask) / float(mask.size)) * 100.0
            diff_stable = (not use_frame_diff_refine) or (diff_motion_percent < config.diff_motion_percent)
            diff_moving = use_frame_diff_refine and (diff_motion_percent >= config.diff_motion_percent)

            if foreground_percent <= config.stable_percent and diff_stable and not capture_enabled:
                capture_enabled = True
                screenshot_count += 1

                output_file = image_dir / f"{screenshot_count:04d}.jpg"
                cv2.imwrite(str(output_file), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            elif capture_enabled and (foreground_percent >= config.motion_percent or diff_moving):
                capture_enabled = False
    finally:
        cap.release()

    return screenshot_count


def _extract_by_frame_diff(
    video_path: Path,
    image_dir: Path,
    config: ProcessingConfig,
    progress_callback: Optional[ProgressCallback] = None,
    stop_callback: Optional[StopCallback] = None,
) -> int:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"unable to open video: {video_path}")

    ret, first_frame = cap.read()
    if not ret:
        cap.release()
        return 0

    screenshot_count = 1
    cv2.imwrite(str(image_dir / f"{screenshot_count:04d}.jpg"), first_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

    prev_frame = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    moving = False
    elapsed = 0
    frame_no = 1
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    try:
        while cap.isOpened():
            _check_cancel(stop_callback)
            ret, frame = cap.read()
            if not ret:
                break

            frame_no += 1
            _emit_frame_progress(progress_callback, frame_no, total_frames)
            if frame_no % config.frame_rate != 0:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame_diff = cv2.absdiff(gray, prev_frame)
            _, frame_diff = cv2.threshold(
                frame_diff,
                config.diff_binary_threshold,
                255,
                cv2.THRESH_BINARY,
            )
            frame_diff = cv2.dilate(frame_diff, kernel)

            motion_percent = (cv2.countNonZero(frame_diff) / float(frame_diff.size)) * 100.0
            if motion_percent >= config.diff_motion_percent and not moving:
                moving = True
                elapsed = 0
            elif moving:
                elapsed += 1

            if moving and elapsed >= config.elapsed_frame_threshold:
                moving = False
                elapsed = 0
                screenshot_count += 1
                cv2.imwrite(
                    str(image_dir / f"{screenshot_count:04d}.jpg"),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 85],
                )

            prev_frame = gray
    finally:
        cap.release()

    return screenshot_count


def _remove_duplicate_images(image_dir: Path, config: ProcessingConfig) -> None:
    image_paths = _list_images(image_dir)
    if len(image_paths) < 2:
        return

    hash_func_name = config.hash_func.lower()
    max_hash_distance = int(config.hash_size * config.hash_size * (100 - config.similarity_threshold) / 100)

    history_hashes = deque(maxlen=config.hash_queue_len)
    exact_hashes = set()

    for image_path in image_paths:
        image_hash = _compute_image_hash(image_path, hash_func_name, config.hash_size)

        is_duplicate = False
        if image_hash in exact_hashes:
            is_duplicate = True
        else:
            for history_hash in history_hashes:
                if _hamming_distance(history_hash, image_hash) <= max_hash_distance:
                    is_duplicate = True
                    break

        if is_duplicate:
            image_path.unlink()
        else:
            exact_hashes.add(image_hash)
            history_hashes.append(image_hash)


def _convert_images_to_pdf(images: Iterable[Path], output_pdf_path: Path) -> None:
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    image_paths = list(images)
    if not image_paths:
        raise RuntimeError("no images found for PDF conversion")

    pil_images: List[Image.Image] = []
    try:
        for image_path in image_paths:
            with Image.open(image_path) as image:
                pil_images.append(image.convert("RGB"))

        first_image, *remaining_images = pil_images
        first_image.save(
            output_pdf_path,
            save_all=True,
            append_images=remaining_images,
            format="PDF",
        )
    finally:
        for image in pil_images:
            image.close()


def _resize_keep_ratio(frame, resize_width: int):
    height, width = frame.shape[:2]
    new_height = int((resize_width * height) / width)
    return cv2.resize(frame, (resize_width, new_height), interpolation=cv2.INTER_AREA)


def _list_images(image_dir: Path) -> List[Path]:
    return sorted(
        [
            path
            for path in image_dir.glob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ],
        key=lambda path: path.name,
    )


def _emit_progress(
    progress_callback: Optional[ProgressCallback],
    progress_value: float,
    message: str,
) -> None:
    if progress_callback is None:
        return
    clamped = min(max(progress_value, 0.0), 1.0)
    progress_callback(clamped, message)


def _check_cancel(stop_callback: Optional[StopCallback]) -> None:
    if stop_callback is not None and stop_callback():
        raise ProcessingCancelled("stopped by user")


def _emit_frame_progress(
    progress_callback: Optional[ProgressCallback],
    frame_no: int,
    total_frames: int,
) -> None:
    if progress_callback is None:
        return
    if total_frames <= 0:
        return
    if frame_no % 30 != 0:
        return

    # Reserve top range for dedup + PDF generation so progress does not jump backwards.
    extraction_progress = 0.05 + (min(frame_no, total_frames) / total_frames) * 0.8
    _emit_progress(progress_callback, extraction_progress, "extracting")


def _compute_image_hash(image_path: Path, hash_func_name: str, hash_size: int) -> int:
    with Image.open(image_path) as image:
        gray = image.convert("L")

        if hash_func_name == "dhash":
            resized = gray.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = np.asarray(resized, dtype=np.uint8)
            diff = pixels[:, 1:] > pixels[:, :-1]
            return _bits_to_int(diff)

        if hash_func_name == "ahash":
            resized = gray.resize((hash_size, hash_size), Image.Resampling.LANCZOS)
            pixels = np.asarray(resized, dtype=np.float32)
            diff = pixels > pixels.mean()
            return _bits_to_int(diff)

        if hash_func_name == "phash":
            # Use OpenCV DCT to avoid scipy dependency and keep container smaller.
            resized = gray.resize((32, 32), Image.Resampling.LANCZOS)
            pixels = np.asarray(resized, dtype=np.float32)
            dct = cv2.dct(pixels)
            low_freq = dct[:hash_size, :hash_size]
            threshold = np.median(low_freq[1:, 1:]) if hash_size > 1 else np.median(low_freq)
            diff = low_freq > threshold
            return _bits_to_int(diff)

    raise ValueError(f"unsupported hash function: {hash_func_name}")


def _bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return value


def _hamming_distance(hash_a: int, hash_b: int) -> int:
    return (hash_a ^ hash_b).bit_count()


def cleanup_video_cache(video_path: Path) -> None:
    video_path = video_path.expanduser().resolve()
    output_pdf_dir = video_path.parent / "pdf"

    image_dir = output_pdf_dir / f"{video_path.stem}_images"
    if image_dir.exists() and image_dir.is_dir():
        for file in image_dir.glob("*"):
            if file.is_file():
                file.unlink(missing_ok=True)
        image_dir.rmdir()

    partial_pdf = output_pdf_dir / f"{video_path.stem}.pdf"
    if partial_pdf.exists() and partial_pdf.is_file():
        partial_pdf.unlink(missing_ok=True)
