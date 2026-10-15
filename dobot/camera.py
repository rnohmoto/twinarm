"""カメラ抽象（OpenCV UVC / RealSense / 静止画ファイル）。

- 俯瞰固定（eye-to-hand）の色認識では「露出・ホワイトバランスの固定」が精度の要。
  OpenCV の自動露出フラグは OS/バックエンドで意味が違う（V4L2: 1=手動,3=自動 / DirectShow: 0.25=手動,0.75=自動）
  ため、両方を試して効いた方を採用する。
- RealSense は pyrealsense2 を遅延 import（未導入でも他モードは動く）。カラーのみ使用（深度は拡張用フック）。
- FileCamera はテスト・ドライラン用（同じ画像を返し続ける）。
- 専用カメラが無いとき（MacBook 内蔵カメラ・iPhone 連係カメラ）: AVFoundation は OpenCV から露出/WB を
  固定できないので、画面内のグレーカード（reference_roi）が毎フレーム一定の明るさ・色になるよう
  チャンネル別ゲインで補正する（露出とホワイトバランスの両方の揺れを吸収）。鏡越し（ミラークリップ）
  で撮るときは mirror=True で鏡像を戻す。処理順は 回転 → 反転 → 補正（ROI・検出・較正は全て補正後の画素）。
"""
from __future__ import annotations

import sys
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


def _normalize_by_reference(frame: np.ndarray, roi, target: int) -> np.ndarray:
    """ROI（グレーカード）の平均が各チャンネル target になるようにゲインを掛ける。

    自動露出・自動 WB が生きているカメラ向け。カードが見えていない（極端に暗い）ときは触らない。
    ゲインは 0.3〜4.0 に抑える（カードが影に入っただけで画面全体が飛ばないように）。
    """
    if roi is None or len(roi) != 4:
        return frame
    h_img, w_img = frame.shape[:2]
    x, y, w, h = (int(v) for v in roi)
    x0, y0 = max(0, min(x, w_img - 1)), max(0, min(y, h_img - 1))
    x1, y1 = max(x0 + 1, min(x + w, w_img)), max(y0 + 1, min(y + h, h_img))
    mean = frame[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32).mean(axis=0)
    if (mean < 8).any():
        return frame
    gain = np.clip(float(target) / mean, 0.3, 4.0)
    return np.clip(frame.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def _postprocess(frame: np.ndarray, cfg: CameraConfig) -> np.ndarray:
    """回転 → 左右反転（鏡越し）→ グレーカード補正。全カメラ共通の出口。"""
    out = _rotate(frame, cfg.rotation_deg)
    if getattr(cfg, "mirror", False):
        out = out[:, ::-1]
    roi = getattr(cfg, "reference_roi", None)
    if roi:
        out = _normalize_by_reference(np.ascontiguousarray(out), roi, getattr(cfg, "reference_gray", 170))
    return np.ascontiguousarray(out)


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
        return _postprocess(self._frame.copy(), self.cfg)


class OpenCVCamera(CameraBase):
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.cap = None
        self.auto_exposure_applied = None
        self.auto_focus_applied = None

    def open(self) -> None:
        import cv2
        idx = self.cfg.index
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        self.cap = None
        if sys.platform == "win32" and isinstance(idx, int):
            # Windows は DirectShow を明示する。MSMF は自動露出の値の意味が違い（0 以外＝自動）、
            # 自動 WB と色温度に対応せず、読み戻しも現在値でない（OpenCV のソースで確認・2026-10）
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                self.cap = cap
        if self.cap is None:
            self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            raise RuntimeError(f"OpenCVCamera: cannot open index {idx}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._apply_exposure_wb()
        self._apply_focus()
        for _ in range(max(0, self.cfg.warmup_frames)):
            self.cap.read()

    # CAP_PROP_AUTO_EXPOSURE の値はバックエンドで意味が違う（実機で必ず --tune の絵と actual_props() で確認）
    #   V4L2 (Linux):   1=manual, 3=auto (aperture priority)
    #   DSHOW / MSMF (Windows), AVFOUNDATION (mac): 0.25=manual, 0.75=auto が慣例
    #   DSHOW: cvRound(v)==1 が自動・それ以外が手動（0.25→手動・0.75→自動）。自動露出の読み戻しは非対応で -1 が返る
    #   MSMF:  0 以外が自動（0.25 も自動になる）→ 手動は 0・自動は 1。読み戻しは現在値でなく既定値
    _AE = {"V4L2": (1, 3), "DSHOW": (0.25, 0.75), "MSMF": (0, 1), "AVFOUNDATION": (0.25, 0.75)}

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
        self.auto_exposure_readback = None
        for v in candidates:
            ok = bool(cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, v))
            got = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
            self.auto_exposure_readback = got
            # 書き込みが受理され、読み戻しが一致するか（DSHOW のように）読み戻し非対応（負値）なら効いたとみなす。
            # 最終判断は check_camera の明るさの揺れ（std・drift）で行う
            if ok and (abs(got - v) < 1e-3 or got < 0):
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

    def _apply_focus(self) -> None:
        """AF を止める（既定）。focus が指定されていればその位置に固定する。

        効いたかは set の戻り値と読み戻しで判定し auto_focus_applied に残す（0=止まった・None=効いていない）。
        AVFoundation（mac）は受け付けないので None のまま → メーカーツールか OS 側で固定する（manual §3）。
        """
        import cv2
        cap = self.cap
        self.auto_focus_applied = None
        if self.cfg.auto_focus:
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            return
        ok = bool(cap.set(cv2.CAP_PROP_AUTOFOCUS, 0))
        got = cap.get(cv2.CAP_PROP_AUTOFOCUS)
        # 読み戻しの意味はバックエンドで違う: V4L2/MSMF は 0=手動・1=自動、DSHOW はフラグ（2=手動・1 や 3=自動）、非対応は負値
        if ok and (got < 0 or round(got) in (0, 2)):
            self.auto_focus_applied = 0
        if self.cfg.focus is not None:
            cap.set(cv2.CAP_PROP_FOCUS, float(self.cfg.focus))

    # C922 は開いた直後の数秒・外部アプリの設定変更の直後にフレームを返さないことがある
    # （実機 2026-10-15: 較正が初回 read で2回即死）。瞬断では諦めず、この秒数読めなければ raise
    READ_RETRY_S = 3.0
    READ_RETRY_INTERVAL_S = 0.1

    def read(self) -> np.ndarray:
        deadline = time.monotonic() + self.READ_RETRY_S
        while True:
            ok, frame = self.cap.read()
            if ok and frame is not None:
                return _postprocess(frame, self.cfg)
            if time.monotonic() >= deadline:
                raise RuntimeError("OpenCVCamera: frame read failed")
            time.sleep(self.READ_RETRY_INTERVAL_S)

    def actual_props(self) -> dict:
        import cv2
        c = self.cap
        return {
            "backend": getattr(self, "backend_name", ""),
            "width": c.get(cv2.CAP_PROP_FRAME_WIDTH), "height": c.get(cv2.CAP_PROP_FRAME_HEIGHT),
            "fps": c.get(cv2.CAP_PROP_FPS), "auto_exposure": c.get(cv2.CAP_PROP_AUTO_EXPOSURE),
            "exposure": c.get(cv2.CAP_PROP_EXPOSURE), "auto_wb": c.get(cv2.CAP_PROP_AUTO_WB),
            "wb_temperature": c.get(cv2.CAP_PROP_WB_TEMPERATURE),
            "auto_focus": c.get(cv2.CAP_PROP_AUTOFOCUS), "focus": c.get(cv2.CAP_PROP_FOCUS),
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
        return _postprocess(np.asanyarray(color.get_data()), self.cfg)

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
