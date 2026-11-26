import ctypes
import gc
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from PyQt5.QtCore import (
    QEasingCurve,
    QMutex,
    QMutexLocker,
    QPropertyAnimation,
    Qt,
    QThread,
    QTimer,
    QWaitCondition,
    pyqtSignal,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

QUALITY_FORMATS = {
    "Best": "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
    "720p": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
    "480p": "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b",
    "Audio Only": "ba/b",
}


if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

if getattr(sys, "frozen", False):
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")


class DownloadCancelled(Exception):
    pass


def create_downloader(options: dict, on_video=None):
    import yt_dlp

    class LeanYoutubeDL(yt_dlp.YoutubeDL):
        def process_info(self, info_dict):
            if on_video:
                on_video(info_dict)
            return super().process_info(info_dict)

        def process_subtitles(self, video_id, normal_subtitles, automatic_captions):
            manual = {
                lang: formats
                for lang, formats in (normal_subtitles or {}).items()
                if lang == "en" or lang.startswith("en-")
            }
            auto = {}
            if not manual:
                for lang in ("en-orig", "en"):
                    formats = [
                        f
                        for f in (automatic_captions or {}).get(lang) or []
                        if "tlang=" not in (f.get("url") or "")
                    ]
                    if formats:
                        auto = {lang: formats}
                        break
            if not manual and not auto:
                return None
            self.params["subtitleslangs"] = list(manual or auto)
            return super().process_subtitles(video_id, manual, auto)

        def _write_subtitles(self, info_dict, filename):
            previous = self.params.get("ignoreerrors")
            self.params["ignoreerrors"] = True
            try:
                return super()._write_subtitles(info_dict, filename)
            finally:
                self.params["ignoreerrors"] = previous

    return LeanYoutubeDL(options)


def format_bytes(num_bytes) -> str:
    if not num_bytes or num_bytes <= 0:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


def format_seconds(seconds) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total_sec = int(seconds)
    mins, secs = divmod(total_sec, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins:02d}m {secs:02d}s"
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def make_simple_error(raw_error: str) -> str:
    msg = raw_error.lower()

    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return (
            "The site is rate limiting you (too many requests). "
            "Wait a while before trying again."
        )
    if "not a bot" in msg:
        return (
            "YouTube wants proof you're not a bot. " "Wait a while before trying again."
        )
    if "requested format is not available" in msg or "no video formats" in msg:
        return (
            "Requested quality/format isn't available for this video. "
            "Switch to Best or another quality, or update yt-dlp."
        )
    if "age" in msg and ("confirm your age" in msg or "inappropriate" in msg):
        return "This video is age-restricted, so this app can't download it."
    if (
        "sign in" in msg
        or "cookies" in msg
        or "private video" in msg
        or "members-only" in msg
    ):
        return "This video needs a login. Only public videos are supported."
    if "not available in your country" in msg or "geo" in msg or "region" in msg:
        return (
            "This video is blocked in your current region. "
            "Try a different link or connect via VPN if needed."
        )
    if "live event will begin" in msg or "premiere" in msg or "is live" in msg:
        return (
            "This looks like a live stream or upcoming premiere rather than a finished video. "
            "Try again once it's available as a standard video."
        )
    if (
        "failed to resolve" in msg
        or "name or service not known" in msg
        or "connection" in msg
        or "timed out" in msg
        or "network is unreachable" in msg
        or "ssl" in msg
    ):
        return (
            "Network connection dropped or timed out. "
            "Check your internet connection or VPN and retry."
        )
    if "ffmpeg" in msg or "postprocessor" in msg or "ffprobe" in msg:
        return (
            "Merging audio, video, or subtitles hit a post-processing issue. "
            "Check that FFmpeg is installed and in your PATH, or try Audio Only."
        )
    if "permission denied" in msg or "access is denied" in msg:
        return (
            "Permission denied while writing to Downloads. "
            "Close any app using the file or check folder permissions."
        )
    if "no space left" in msg or "disk full" in msg:
        return (
            "Not enough free disk space in your Downloads drive. "
            "Free up some storage space and try again."
        )
    if (
        "unsupported url" in msg
        or "not a valid url" in msg
        or "is not a valid url" in msg
    ):
        return (
            "That link doesn't look valid or supported. "
            "Paste a full video URL and try again."
        )
    if (
        "video unavailable" in msg
        or "this video has been removed" in msg
        or "private" in msg
    ):
        return (
            "This video is unavailable, private, or removed. "
            "Check the link in your browser or paste another video URL."
        )

    return (
        "Download couldn't finish. Check that the link works in your browser, "
        "ensure internet & FFmpeg are working, and try another link."
    )


def exact_size(info: dict) -> int:
    parts = info.get("requested_formats") or [info]
    sizes = [p.get("filesize") or 0 for p in parts]
    return int(sum(sizes)) if all(sizes) else 0


def _colorref_from_hex(hex_color: str) -> int:
    cleaned = hex_color.lstrip("#")
    r = int(cleaned[0:2], 16)
    g = int(cleaned[2:4], 16)
    b = int(cleaned[4:6], 16)
    return (b << 16) | (g << 8) | r


class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    stage = pyqtSignal(str)
    stats = pyqtSignal(str)
    detected_quality = pyqtSignal(str)
    paused_state = pyqtSignal(bool)
    download_done = pyqtSignal()
    cancelled = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        format_spec: str,
        audio_only: bool = False,
    ):
        super().__init__()
        self.final_bytes = 0
        self.cleanup_failed = False
        self.url = url
        self.format_spec = format_spec
        self.audio_only = audio_only
        self.last_reported_quality = ""
        self._mutex = QMutex()
        self._pause_cond = QWaitCondition()
        self._is_paused = False
        self._is_cancelled = False
        self._downloads_dir = Path.home() / "Downloads"
        key = hashlib.sha1(f"{url}|{format_spec}".encode()).hexdigest()[:12]
        self._temp_dir = self._downloads_dir / f".ytdl-tmp-{key}"

    def pause(self):
        with QMutexLocker(self._mutex):
            if not self._is_paused and not self._is_cancelled:
                self._is_paused = True
                self.paused_state.emit(True)

    def resume(self):
        with QMutexLocker(self._mutex):
            if self._is_paused:
                self._is_paused = False
                self._pause_cond.wakeAll()
                self.paused_state.emit(False)

    def cancel(self):
        with QMutexLocker(self._mutex):
            self._is_cancelled = True
            self._is_paused = False
            self._pause_cond.wakeAll()

    def _is_cancelled_flag(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._is_cancelled

    def _check_pause_or_cancel(self):
        with QMutexLocker(self._mutex):
            while self._is_paused and not self._is_cancelled:
                self._pause_cond.wait(self._mutex)
            if self._is_cancelled:
                raise DownloadCancelled()

    def _remove_temp_dir(self):
        for _ in range(20):
            gc.collect()
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            if not self._temp_dir.exists():
                return
            self.msleep(250)
        self.cleanup_failed = True

    def run(self):
        last_emit = 0.0
        total_size = 0
        part_bytes = {}

        def video_hook(info):
            nonlocal total_size
            total_size = exact_size(info)
            part_bytes.clear()
            if total_size:
                self.stats.emit(f"Size: {format_bytes(total_size)}")

        def progress_hook(data):
            nonlocal last_emit
            self._check_pause_or_cancel()

            status = data.get("status")
            now = time.monotonic()
            if status == "downloading" and now - last_emit < 0.25:
                return
            last_emit = now

            info = data.get("info_dict") or {}
            ext = (info.get("ext") or "").lower()
            vcodec = info.get("vcodec")
            acodec = info.get("acodec")
            height = info.get("height")

            if height and height > 0 and not self.audio_only:
                q_label = f"{height}p"
                if q_label != self.last_reported_quality:
                    self.last_reported_quality = q_label
                    self.detected_quality.emit(q_label)

            if ext in ("vtt", "srt", "ass", "lrc", "ttml"):
                if status == "downloading":
                    self.stage.emit("Downloading subtitles...")
                return

            downloaded = data.get("downloaded_bytes") or 0
            part_total = data.get("total_bytes") or 0
            eta = data.get("eta")

            if total_size:
                if status == "finished":
                    downloaded = downloaded or part_total
                part_bytes[data.get("filename")] = downloaded
                downloaded = sum(part_bytes.values())
                total, exact = total_size, True
                speed = data.get("speed")
                if speed:
                    eta = max(total - downloaded, 0) / speed
            else:
                total = part_total or data.get("total_bytes_estimate") or 0
                exact = bool(part_total)

            mark = "" if exact else "~"
            if total > 0:
                size_text = f"{format_bytes(downloaded)} / {mark}{format_bytes(total)}"
                self.progress.emit(min(int(downloaded * 100 / total), 100))
            elif downloaded > 0:
                size_text = format_bytes(downloaded)
            else:
                size_text = "Calculating size..."

            if status == "downloading":
                if vcodec == "none" and acodec != "none":
                    self.stage.emit("Downloading audio...")
                else:
                    self.stage.emit("Downloading video...")
                self.stats.emit(
                    f"Size: {size_text}   |   Time left: {format_seconds(eta)}"
                )
            elif status == "finished":
                self.stage.emit("Verifying downloaded part...")
                self.stats.emit(f"Size: {size_text}")

        def postprocessor_hook(data):
            self._check_pause_or_cancel()
            if data.get("status") == "started":
                self.stage.emit("Merging video, audio, and verifying file...")
                self.stats.emit("Finalizing file on disk...")

        def saved_hook(path):
            if Path(path).is_file():
                self.final_bytes = Path(path).stat().st_size

        ydl_opts = {
            "format": self.format_spec,
            "remote_components": ["ejs:github"],
            "sleep_interval_requests": 0.75,
            "continuedl": True,
            "paths": {
                "home": str(self._downloads_dir),
                "temp": str(self._temp_dir),
            },
            "outtmpl": "%(title)s.%(ext)s",
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "post_hooks": [saved_hook],
        }

        if not self.audio_only:
            ydl_opts.update(
                {
                    "writesubtitles": True,
                    "writeautomaticsub": True,
                    "postprocessors": [
                        {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False}
                    ],
                    "merge_output_format": "mp4",
                }
            )

        was_cancelled = False
        try:
            self._check_pause_or_cancel()
            self.stage.emit("Fetching video info...")
            self.stats.emit("Size: —   |   Time left: —")
            with create_downloader(ydl_opts, on_video=video_hook) as ydl:
                ydl.download([self.url])

            self._remove_temp_dir()
            self.download_done.emit()
        except DownloadCancelled:
            was_cancelled = True
        except Exception as exc:
            if self._is_cancelled_flag():
                was_cancelled = True
            else:
                self.failed.emit(str(exc))

        if was_cancelled:
            self._remove_temp_dir()
            self.cancelled.emit()
        gc.collect()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.current_percent = 0
        self.current_task = "Ready"
        self.active_quality_tag = "Best"
        self.anim_step = 0
        self.selected_quality = "Best"
        self.dropdown_open = False
        self.is_paused = False

        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(140)
        self.anim_timer.timeout.connect(self.tick_animation)

        self.reset_timer = QTimer(self)
        self.reset_timer.setSingleShot(True)
        self.reset_timer.setInterval(4000)
        self.reset_timer.timeout.connect(self.reset_for_next_link)

        self.url_box = QLineEdit()

        self.quality_button = QPushButton(f"Quality: {self.selected_quality}   ▾")
        self.quality_button.setObjectName("qualityButton")
        self.quality_button.clicked.connect(self.toggle_quality_drawer)

        self.quality_drawer = QFrame()
        self.quality_drawer.setObjectName("qualityDrawer")
        self.quality_drawer.setMaximumHeight(0)
        self.quality_drawer.hide()
        drawer_layout = QVBoxLayout(self.quality_drawer)
        drawer_layout.setContentsMargins(4, 4, 4, 4)
        drawer_layout.setSpacing(2)

        self.quality_option_buttons = {}
        for label in QUALITY_FORMATS:
            btn = QPushButton(label)
            btn.setObjectName("qualityOption")
            btn.setFixedHeight(34)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, q=label: self.select_quality(q))
            drawer_layout.addWidget(btn)
            self.quality_option_buttons[label] = btn

        self.drawer_target_height = len(QUALITY_FORMATS) * 36 + 8
        self.drawer_anim = QPropertyAnimation(
            self.quality_drawer, b"maximumHeight", self
        )
        self.drawer_anim.setDuration(130)
        self.drawer_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.drawer_anim.finished.connect(self.on_drawer_anim_finished)

        self.action_row = QWidget()
        action_layout = QHBoxLayout(self.action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(10)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.setFixedHeight(52)
        self.pause_button.clicked.connect(self.toggle_pause)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setFixedHeight(52)
        self.cancel_button.clicked.connect(self.cancel_download)

        action_layout.addWidget(self.pause_button)
        action_layout.addWidget(self.cancel_button)
        self.action_row.hide()

        self.enter_button = QPushButton("Download")

        self.status_panel = QWidget()
        self.status_panel.setObjectName("statusPanel")
        panel_layout = QVBoxLayout(self.status_panel)
        panel_layout.setContentsMargins(14, 12, 14, 12)
        panel_layout.setSpacing(8)

        self.anim_label = QLabel("[ ● · · · ]")
        self.anim_label.setObjectName("animLabel")
        self.anim_label.setAlignment(Qt.AlignCenter)

        self.status_label = QLabel("Downloading... 0%")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        self.message_label = QLabel("")
        self.message_label.setObjectName("messageLabel")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("statsLabel")
        self.stats_label.setAlignment(Qt.AlignCenter)
        self.stats_label.setWordWrap(True)

        panel_layout.addWidget(self.anim_label)
        panel_layout.addWidget(self.status_label)
        panel_layout.addWidget(self.progress_bar)
        panel_layout.addWidget(self.message_label)
        panel_layout.addWidget(self.stats_label)
        self.status_panel.hide()

        self.enter_button.clicked.connect(self.download_video)
        self.url_box.returnPressed.connect(self.download_video)

        self.init_ui()
        self.sync_quality_selection_styles()

    def set_url_locked(self, locked: bool):
        self.url_box.setReadOnly(locked)
        self.url_box.setProperty("locked", "true" if locked else "false")
        self.url_box.style().unpolish(self.url_box)
        self.url_box.style().polish(self.url_box)

    def sync_quality_selection_styles(self):
        for label, btn in self.quality_option_buttons.items():
            is_selected = label == self.selected_quality
            btn.setProperty("selected", "true" if is_selected else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _animate_drawer_to(self, target_height: int, opening: bool):
        self.dropdown_open = opening
        if opening:
            self.quality_drawer.show()
        self.drawer_anim.stop()
        self.drawer_anim.setStartValue(self.quality_drawer.maximumHeight())
        self.drawer_anim.setEndValue(target_height)
        self.drawer_anim.start()

    def toggle_quality_drawer(self):
        if not self.dropdown_open:
            self._animate_drawer_to(self.drawer_target_height, opening=True)
        else:
            self._animate_drawer_to(0, opening=False)

    def close_quality_drawer_instant(self):
        self.drawer_anim.stop()
        self.dropdown_open = False
        self.quality_drawer.setMaximumHeight(0)
        self.quality_drawer.hide()

    def on_drawer_anim_finished(self):
        if not self.dropdown_open and self.quality_drawer.maximumHeight() == 0:
            self.quality_drawer.hide()

    def select_quality(self, quality_label: str):
        self.selected_quality = quality_label
        self.quality_button.setText(f"Quality: {self.selected_quality}   ▾")
        self.sync_quality_selection_styles()
        self._animate_drawer_to(0, opening=False)
        if not self.url_box.isReadOnly():
            self.url_box.setFocus()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if not self.worker.wait(10000):
                self.worker.terminate()
                self.worker.wait()
        super().closeEvent(event)

    def init_ui(self):
        self.setWindowTitle("YT Downloader")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icon1.png"))))
        self.setGeometry(100, 100, 460, 560)

        self.url_box.setFixedHeight(60)
        self.url_box.setPlaceholderText("Enter the URL")
        self.quality_button.setFixedHeight(48)
        self.enter_button.setFixedHeight(60)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.addStretch()
        layout.addWidget(self.url_box)
        layout.addWidget(self.status_panel)
        layout.addWidget(self.quality_button)
        layout.addWidget(self.quality_drawer)
        layout.addWidget(self.action_row)
        layout.addWidget(self.enter_button)
        layout.addStretch()
        self.setLayout(layout)
        self.set_titlebar_style()

        self.setStyleSheet("""
            QWidget {
                background-color: #202124;
            }
            QLineEdit {
                font-size: 20px;
                font-family: "Segoe UI";
                background-color: #ffffff;
                color: #202124;
                padding: 8px;
                border: 2px solid #e53935;
                border-radius: 8px;
            }
            QLineEdit[locked="true"] {
                background-color: #d8dce2;
                color: #3c4043;
                border: 2px solid #7f2a28;
            }
            QPushButton {
                font-size: 20px;
                font-family: "Segoe UI";
                font-weight: bold;
                background-color: #e53935;
                color: #ffffff;
                padding: 10px 18px;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #c62828;
            }
            QPushButton:disabled {
                background-color: #7f2a28;
                color: #e0e0e0;
            }
            QPushButton#pauseButton {
                font-size: 16px;
                background-color: #3c4043;
                color: #ffffff;
                border: 1px solid #6c727a;
                padding: 8px 14px;
            }
            QPushButton#pauseButton:hover {
                background-color: #4d5257;
            }
            QPushButton#cancelButton {
                font-size: 16px;
                background-color: #2c2d31;
                color: #ff8a80;
                border: 1px solid #c62828;
                padding: 8px 14px;
            }
            QPushButton#cancelButton:hover {
                background-color: #4a1c1c;
                color: #ffffff;
            }
            QPushButton#qualityButton {
                font-size: 16px;
                font-weight: bold;
                background-color: #2c2d31;
                color: #ffffff;
                border: 1px solid #555861;
                border-radius: 8px;
                padding: 8px 14px;
            }
            QPushButton#qualityButton:hover {
                background-color: #383a40;
            }
            QFrame#qualityDrawer {
                background-color: #26272b;
                border: 1px solid #44474e;
                border-radius: 8px;
            }
            QPushButton#qualityOption {
                font-size: 15px;
                font-weight: normal;
                text-align: left;
                background-color: transparent;
                color: #e8eaed;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton#qualityOption:hover {
                background-color: #383a40;
                color: #ffffff;
            }
            QPushButton#qualityOption[selected="true"] {
                background-color: #e53935;
                color: #ffffff;
                font-weight: bold;
            }
            QWidget#statusPanel {
                background-color: #2c2d31;
                border: 1px solid #44474e;
                border-radius: 8px;
            }
            QWidget#statusPanel QLabel {
                color: #ffffff;
                font-family: "Segoe UI";
                background: transparent;
            }
            QLabel#animLabel {
                color: #ff6b6b;
                font-family: "Consolas", monospace;
                font-size: 18px;
                font-weight: bold;
            }
            QLabel#statusLabel {
                font-size: 18px;
                font-weight: bold;
            }
            QLabel#messageLabel {
                font-size: 16px;
            }
            QLabel#statsLabel {
                font-size: 15px;
                color: #e8eaed;
            }
            QProgressBar {
                color: #ffffff;
                font-family: "Segoe UI";
                font-size: 15px;
                font-weight: bold;
                background-color: #1b1c1f;
                border: 1px solid #555861;
                border-radius: 6px;
                text-align: center;
                height: 26px;
            }
            QProgressBar::chunk {
                background-color: #e53935;
                border-radius: 5px;
            }
        """)

    def refresh_status_text(self):
        prefix = "Paused" if self.is_paused else self.current_task
        self.status_label.setText(
            f"{prefix} [{self.active_quality_tag}] {self.current_percent}%"
        )

    def tick_animation(self):
        if self.is_paused:
            self.anim_label.setText("[ ❚❚ PAUSE ]")
            self.refresh_status_text()
            return

        frames = [
            "[ ● · · · ]",
            "[ · ● · · ]",
            "[ · · ● · ]",
            "[ · · · ● ]",
            "[ · · ● · ]",
            "[ · ● · · ]",
        ]
        self.anim_label.setText(frames[self.anim_step % len(frames)])
        self.anim_step += 1
        self.refresh_status_text()

    def toggle_pause(self):
        if self.worker is None or not self.worker.isRunning():
            return
        if not self.is_paused:
            self.worker.pause()
        else:
            self.worker.resume()

    def on_paused_state_changed(self, paused: bool):
        self.is_paused = paused
        if paused:
            self.pause_button.setText("Resume")
            self.anim_label.setText("[ ❚❚ PAUSE ]")
            self.message_label.setText(
                "Download paused. Click Resume to continue where it left off."
            )
        else:
            self.pause_button.setText("Pause")
            self.message_label.setText("Resuming download...")
        self.refresh_status_text()

    def cancel_download(self):
        if self.worker is None:
            return
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.message_label.setText("Cancelling and deleting downloaded files...")
        self.worker.cancel()

    def download_video(self):
        if self.worker is not None and self.worker.isRunning():
            return

        self.close_quality_drawer_instant()
        self.reset_timer.stop()
        link = self.url_box.text().strip()
        self.status_panel.show()

        if not link:
            self.set_url_locked(False)
            self.action_row.hide()
            self.anim_timer.stop()
            self.anim_label.setText("[ ! · · · ]")
            self.progress_bar.setValue(0)
            self.status_label.setText("No link pasted")
            self.message_label.setText(
                "Paste a video link above and press Enter or click Download."
            )
            self.stats_label.setText("")
            self.url_box.setFocus()
            return

        format_spec = QUALITY_FORMATS.get(
            self.selected_quality, QUALITY_FORMATS["Best"]
        )
        audio_only = self.selected_quality == "Audio Only"

        self.is_paused = False
        self.pause_button.setText("Pause")
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.action_row.show()

        self.set_url_locked(True)
        self.current_percent = 0
        self.current_task = "Starting"
        self.active_quality_tag = self.selected_quality
        self.anim_step = 0
        self.progress_bar.setValue(0)
        self.refresh_status_text()
        self.message_label.setText(f"Preparing {self.selected_quality} download...")
        self.stats_label.setText("Size: —   |   Time left: —")
        self.quality_button.setEnabled(False)
        self.enter_button.setEnabled(False)
        self.anim_timer.start()

        if self.worker is not None:
            self.worker.wait()
            self.worker.deleteLater()
        self.worker = DownloadWorker(
            link,
            format_spec,
            audio_only=audio_only,
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.stage.connect(self.update_stage)
        self.worker.stats.connect(self.update_stats)
        self.worker.detected_quality.connect(self.update_detected_quality)
        self.worker.paused_state.connect(self.on_paused_state_changed)
        self.worker.download_done.connect(self.download_finished)
        self.worker.cancelled.connect(self.download_cancelled)
        self.worker.failed.connect(self.download_failed)
        self.worker.start()

    def update_detected_quality(self, detected_res: str):
        if self.selected_quality == "Best":
            self.active_quality_tag = f"Best · {detected_res}"
            self.refresh_status_text()

    def update_stage(self, stage_text: str):
        self.current_task = stage_text.rstrip(".")
        if not self.is_paused:
            self.message_label.setText(stage_text)
        self.refresh_status_text()

    def update_stats(self, stats_text: str):
        self.stats_label.setText(stats_text)

    def update_progress(self, percent: int):
        self.current_percent = percent
        self.progress_bar.setValue(percent)
        self.refresh_status_text()

    def download_finished(self):
        self.is_paused = False
        self.action_row.hide()
        self.anim_timer.stop()
        self.anim_label.setText("[ ✔ DONE  ]")
        self.progress_bar.setValue(100)
        self.current_percent = 100
        self.status_label.setText(f"Download complete [{self.active_quality_tag}] 100%")
        self.message_label.setText(
            "Saved to Downloads. Cleaning up for the next link..."
        )
        size = self.worker.final_bytes if self.worker else 0
        self.stats_label.setText(f"File size: {format_bytes(size)}" if size else "")
        self.quality_button.setEnabled(True)
        self.enter_button.setEnabled(True)
        self.reset_timer.start()

    def download_cancelled(self):
        self.is_paused = False
        self.action_row.hide()
        self.anim_timer.stop()
        self.anim_label.setText("[ ■ CLEAN ]")
        self.progress_bar.setValue(0)
        self.current_percent = 0
        self.status_label.setText("Download cancelled")
        if self.worker is not None and self.worker.cleanup_failed:
            self.message_label.setText(
                "Cancelled, but some files are in use. Delete the .ytdl-tmp "
                "folder in Downloads manually."
            )
        else:
            self.message_label.setText(
                "Cancelled and deleted downloaded files. Ready for another link."
            )
        self.stats_label.setText("")
        self.set_url_locked(False)
        self.url_box.clear()
        self.quality_button.setEnabled(True)
        self.enter_button.setEnabled(True)
        self.url_box.setFocus()

    def reset_for_next_link(self):
        self.set_url_locked(False)
        self.url_box.clear()
        self.progress_bar.setValue(0)
        self.current_percent = 0
        self.active_quality_tag = self.selected_quality
        self.message_label.setText("")
        self.stats_label.setText("")
        self.status_panel.hide()
        self.url_box.setFocus()

    def download_failed(self, raw_error: str):
        self.is_paused = False
        self.action_row.hide()
        self.set_url_locked(False)
        self.anim_timer.stop()
        self.anim_label.setText("[ ✖ ERROR ]")
        print(f"yt-dlp error: {raw_error}")
        self.status_label.setText("Download failed")
        self.message_label.setText(make_simple_error(raw_error))
        self.stats_label.setText("")
        self.quality_button.setEnabled(True)
        self.enter_button.setEnabled(True)
        self.url_box.selectAll()
        self.url_box.setFocus()

    def set_titlebar_style(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwmapi = ctypes.windll.dwmapi

            use_dark_mode = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                20,
                ctypes.byref(use_dark_mode),
                ctypes.sizeof(use_dark_mode),
            )

            dark_caption_color = ctypes.c_uint(_colorref_from_hex("#111827"))
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                35,
                ctypes.byref(dark_caption_color),
                ctypes.sizeof(dark_caption_color),
            )
        except Exception:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
