"""カメラ抽象（OpenCV UVC / RealSense / 静止画ファイル）。

- 俯瞰固定（eye-to-hand）の色認識では「露出・ホワイトバランスの固定」が精度の要。
  OpenCV の自動露出フラグは OS/バックエンドで意味が違う（V4L2: 1=手動,3=自動 / DirectShow: 0.25=手動,0.75=自動）
  ため、両方を試して効いた方を採用する。
- RealSense は pyrealsense2 を遅延 import（未導入でも他モードは動く）。カラーのみ使用（深度は拡張用フック）。
- FileCamera はテスト・ドライラン用（同じ画像を返し続ける）。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from config import CameraConfig


class CameraBase:
    def open(self) -> None: ...
    def read(self) -> np.ndarray:  # BGR uint8 (H, W, 3)
        raise NotImplementedError
    def close(self) -> None: ...
    def __enter__(self):
        self.open()
        return self
    def __exit__(self, *exc):
        self.close()


def _rotate(frame: np.ndarray, deg: int) -> np.ndarray:
    import cv2
    if deg % 360 == 0:
        return frame
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[deg % 360]
    return cv2.rotate(frame, code)


class FileCamera(CameraBase):
    """画像ファイル（または ndarray）を固定フレームとして返す。"""
    def __init__(self, cfg: CameraConfig, frame: np.ndarray | None = None):
        self.cfg = cfg
        self._frame = frame

    def open(self) -> None:
        if self._frame is None:
            import cv2
            p = Path(str(self.cfg.index))
            img = cv2.imread(str(p))
            if img is None:
                raise FileNotFoundError(f"FileCamera: cannot read {p}")
            self._frame = img

    def read(self) -> np.ndarray:
        assert self._frame is not None, "FileCamera not opened"
        return _rotate(self._frame.copy(), self.cfg.rotation_deg)


class OpenCVCamera(CameraBase):
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.cap = None

    def open(self) -> None:
        import cv2
        idx = self.cfg.index
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            raise RuntimeError(f"OpenCVCamera: cannot open index {idx}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._apply_exposure_wb()
        for _ in range(max(0, self.cfg.warmup_frames)):
            self.cap.read()

    # CAP_PROP_AUTO_EXPOSURE の値はバックエンドで意味が違う（実機で必ず --tune の絵と actual_props() で確認）
    #   V4L2 (Linux):   1=manual, 3=auto (aperture priority)
    #   DSHOW / MSMF (Windows), AVFOUNDATION (mac): 0.25=manual, 0.75=auto が慣例
    _AE = {"V4L2": (1, 3), "DSHOW": (0.25, 0.75), "MSMF": (0.25, 0.75), "AVFOUNDATION": (0.25, 0.75)}

    def _apply_exposure_wb(self) -> None:
        import cv2
        cap = self.cap
        backend = ""
        try:
            backend = cap.getBackendName().upper()
        except Exception:
            pass
        manual, auto = self._AE.get(backend, (0.25, 0.75))
        # 一部の Windows ドライバは 0=manual/1=auto と報告されている（D 調査・☆）ため、
        # 第一候補が読み戻しで効いていなければ代替値も試す。最終判断は --tune の絵で行う
        candidates = [manual, 0.25, 1, 0] if not self.cfg.auto_exposure else [auto, 0.75, 3, 1]
        applied = None
        for v in candidates:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, v)
            got = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
            if abs(got - v) < 1e-3:
                applied = v
                break
        if not self.cfg.auto_exposure:
            cap.set(cv2.CAP_PROP_EXPOSURE, self.cfg.exposure)
        self.backend_name = backend
        self.auto_exposure_applied = applied
        if not self.cfg.auto_wb:
            cap.set(cv2.CAP_PROP_AUTO_WB, 0)
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE, self.cfg.wb_temperature)
        else:
            cap.set(cv2.CAP_PROP_AUTO_WB, 1)

    def read(self) -> np.ndarray:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            # 1回だけ再試行（USBの瞬断対策）
            time.sleep(0.05)
            ok, frame = self.cap.read()
            if not ok or frame is None:
                raise RuntimeError("OpenCVCamera: frame read failed")
        return _rotate(frame, self.cfg.rotation_deg)

    def actual_props(self) -> dict:
        import cv2
        c = self.cap
        return {
            "backend": getattr(self, "backend_name", ""),
            "width": c.get(cv2.CAP_PROP_FRAME_WIDTH), "height": c.get(cv2.CAP_PROP_FRAME_HEIGHT),
            "fps": c.get(cv2.CAP_PROP_FPS), "auto_exposure": c.get(cv2.CAP_PROP_AUTO_EXPOSURE),
            "exposure": c.get(cv2.CAP_PROP_EXPOSURE), "auto_wb": c.get(cv2.CAP_PROP_AUTO_WB),
            "wb_temperature": c.get(cv2.CAP_PROP_WB_TEMPERATURE),
        }

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class RealSenseCamera(CameraBase):
    """Intel/RealSense D4xx のカラーストリーム（深度は read_depth() で任意取得）。

    pyrealsense2 が必要。LeRobot の RealSenseCamera と同じく serial で機体を指定できる
    （cfg.index に文字列のシリアル、または None で先頭機）。
    """
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.pipeline = None
        self._rs = None

    def open(self) -> None:
        import pyrealsense2 as rs  # type: ignore
        self._rs = rs
        self.pipeline = rs.pipeline()
        conf = rs.config()
        if isinstance(self.cfg.index, str) and self.cfg.index and not self.cfg.index.isdigit():
            conf.enable_device(self.cfg.index)
        conf.enable_stream(rs.stream.color, self.cfg.width, self.cfg.height, rs.format.bgr8, self.cfg.fps)
        conf.enable_stream(rs.stream.depth, self.cfg.width, self.cfg.height, rs.format.z16, self.cfg.fps)
        profile = self.pipeline.start(conf)
        # 露出/WB 固定（RGB モジュールがある機種のみ。D405 は非対応=例外を握って続行）
        try:
            color_sensor = profile.get_device().first_color_sensor()
            if not self.cfg.auto_exposure:
                color_sensor.set_option(rs.option.enable_auto_exposure, 0)
                color_sensor.set_option(rs.option.exposure, float(abs(self.cfg.exposure)))
            if not self.cfg.auto_wb:
                color_sensor.set_option(rs.option.enable_auto_white_balance, 0)
                color_sensor.set_option(rs.option.white_balance, float(self.cfg.wb_temperature))
        except Exception:
            pass
        for _ in range(max(0, self.cfg.warmup_frames)):
            self.pipeline.wait_for_frames()

    def read(self) -> np.ndarray:
        frames = self.pipeline.wait_for_frames()
        color = frames.get_color_frame()
        if not color:
            raise RuntimeError("RealSenseCamera: no color frame")
        return _rotate(np.asanyarray(color.get_data()), self.cfg.rotation_deg)

    def read_depth(self) -> np.ndarray:
        """深度 (H, W) uint16, mm 単位（拡張用: 高さ違い・積み重ねの判定）。"""
        frames = self.pipeline.wait_for_frames()
        depth = frames.get_depth_frame()
        return np.asanyarray(depth.get_data())

    def close(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()
            self.pipeline = None


def make_camera(cfg: CameraConfig, frame: np.ndarray | None = None) -> CameraBase:
    if cfg.backend == "file":
        return FileCamera(cfg, frame)
    if cfg.backend == "realsense":
        return RealSenseCamera(cfg)
    if cfg.backend == "opencv":
        return OpenCVCamera(cfg)
    raise ValueError(f"unknown camera backend: {cfg.backend}")


def list_opencv_cameras(max_index: int = 8) -> list[dict]:
    """接続カメラの簡易探索（LeRobot の `lerobot-find-cameras opencv` 相当）。"""
    import cv2
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, _ = cap.read()
            found.append({"index": i, "readable": bool(ok),
                          "width": cap.get(cv2.CAP_PROP_FRAME_WIDTH), "height": cap.get(cv2.CAP_PROP_FRAME_HEIGHT)})
        cap.release()
    return found


if __name__ == "__main__":
    for c in list_opencv_cameras():
        print(c)
