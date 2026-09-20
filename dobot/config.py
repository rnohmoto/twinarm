"""magician_pnp 設定（JSON永続化・依存ゼロ）。

設計方針
- 「LLMに座標を作らせない」: ロボット座標系で意味を持つのは、この設定に登録した
  ゾーン（置き場）と、キャリブレーション（ホモグラフィ）で画像から変換した検出結果だけ。
- 単位: ロボット座標は mm（Dobot Magician のベース座標系）。画像はピクセル。
- 既定値は「卓上に 3cm 角の発泡ブロック（赤/緑/青/黄）を並べ、俯瞰カメラで拾う」想定。
  実機で必ず `calibrate.py` と `main.py --tune` で上書きする（既定値のまま動かさない）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CameraConfig:
    backend: str = "opencv"          # opencv | realsense | file
    index: Any = 0                   # OpenCV: int または デバイスパス。file: 画像パス
    width: int = 1280
    height: int = 720
    fps: int = 30
    auto_exposure: bool = False      # 展示では固定（HSVの安定に必須）
    exposure: float = -6.0           # OpenCV CAP_PROP_EXPOSURE（Windows: 2^n 秒・Linux: 100µs単位で機種依存）
    auto_wb: bool = False
    wb_temperature: int = 4500       # CAP_PROP_WB_TEMPERATURE
    rotation_deg: int = 0            # 0/90/180/270（俯瞰の向き合わせ）
    warmup_frames: int = 10          # 露出安定待ち


@dataclass
class RobotConfig:
    backend: str = "dry"             # dry | pydobot
    port: str | None = None          # None=自動探索（CP210x / Dobot）
    end_effector: str = "suction"    # suction | gripper
    ee_settle_s: float = 0.6         # 吸着/把持が効くまでの待ち
    z_safe: float = 40.0             # 移動時の退避高さ（テーブル面基準ではなく座標値）
    z_pick: float = -30.0            # 物体上面に吸盤が当たる高さ（実機で較正して上書き）
    z_place: float = -25.0           # 置くときの高さ
    r: float = 0.0                   # 手先回転（吸盤では未使用）
    velocity: float = 150.0          # pydobot speed(velocity, acceleration)
    acceleration: float = 150.0
    # 作業範囲ガード（ベース原点=mm）。Magician のリーチ 320mm（公式）と根元干渉域を避ける
    x_min: float = 150.0
    x_max: float = 300.0
    y_min: float = -150.0
    y_max: float = 150.0
    z_min: float = -60.0
    z_max: float = 120.0
    reach_max: float = 315.0
    reach_min: float = 140.0
    home_xyzr: tuple[float, float, float, float] = (200.0, 0.0, 50.0, 0.0)


@dataclass
class Zone:
    """置き場。LLM/ルール解析はここに登録した名前・別名でしか目的地を指せない。"""
    name: str
    aliases: list[str]
    x: float
    y: float
    z_place: float | None = None     # None なら RobotConfig.z_place


@dataclass
class ColorRange:
    """HSV しきい値（OpenCV: H=0..179, S=0..255, V=0..255）。赤はH両端を跨ぐので複数レンジ。"""
    name: str
    aliases: list[str]
    ranges: list[list[list[int]]]    # [[[h_lo,s_lo,v_lo],[h_hi,s_hi,v_hi]], ...]


@dataclass
class LLMConfig:
    enabled: bool = True
    provider: str = "anthropic"      # anthropic | none（none=ルールベース解析のみ）
    model: str = "claude-opus-5"     # 既定（claude-api skill 規約）。遅延優先なら claude-haiku-4-5 をユーザー判断で
    effort: str = "low"              # low | medium | high（展示は応答速度優先で low）
    max_tokens: int = 1024
    max_turns: int = 6               # ツール呼び出しループの上限
    history_turns: int = 4           # 直近の会話を何往復持つか（「もう一個」対応）
    timeout_s: float = 20.0


@dataclass
class ASRConfig:
    engine: str = "faster_whisper"   # faster_whisper | text（キーボード入力）
    model: str = "small"             # faster-whisper のモデル名 or ローカルパス（例: kotoba-tech/kotoba-whisper-v2.0-faster）
    device: str = "cpu"              # cpu | cuda
    compute_type: str = "int8"       # cpu なら int8
    language: str = "ja"
    sample_rate: int = 16000
    max_record_s: float = 6.0        # push-to-talk の最長録音
    silence_rms: float = 0.01        # 無音判定（正規化振幅）
    silence_hold_s: float = 0.8      # 無音がこの秒数続いたら録音終了


@dataclass
class TTSConfig:
    engine: str = "auto"             # auto | voicevox | say | sapi | print
    voicevox_url: str = "http://127.0.0.1:50021"
    voicevox_speaker: int = 3        # ずんだもん(ノーマル)=3 等。VOICEVOX 側の一覧で確認
    blocking: bool = False


@dataclass
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    robot: RobotConfig = field(default_factory=RobotConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    zones: list[Zone] = field(default_factory=list)
    colors: list[ColorRange] = field(default_factory=list)
    homography_path: str = "assets/homography.json"
    min_area_px: int = 400           # 検出の最小面積（1280x720・高さ50cm・3cm角で概ね 2000〜4000px）
    max_area_px: int = 60000
    log_dir: str = "logs"
    panel_window: bool = True        # OpenCV ウィンドウでパネル表示

    # ---------------------------------------------------------------- defaults
    @staticmethod
    def default() -> AppConfig:
        cfg = AppConfig()
        cfg.zones = [
            Zone("tray_right", ["右", "みぎ", "右のトレイ", "右側", "右のお皿", "right"], 230.0, -110.0),
            Zone("tray_left", ["左", "ひだり", "左のトレイ", "左側", "左のお皿", "left"], 230.0, 110.0),
            Zone("box_front", ["手前", "てまえ", "前", "手前の箱", "front"], 170.0, 0.0),
            Zone("box_back", ["奥", "おく", "後ろ", "奥の箱", "back"], 280.0, 0.0),
        ]
        cfg.colors = [
            ColorRange("red", ["赤", "あか", "レッド", "赤い", "赤色"],
                       [[[0, 120, 70], [8, 255, 255]], [[170, 120, 70], [179, 255, 255]]]),
            ColorRange("green", ["緑", "みどり", "グリーン", "緑の", "緑色", "青緑"],
                       [[[40, 80, 60], [85, 255, 255]]]),
            ColorRange("blue", ["青", "あお", "ブルー", "青い", "青色", "水色"],
                       [[[95, 120, 60], [130, 255, 255]]]),
            ColorRange("yellow", ["黄", "きいろ", "黄色", "イエロー", "黄色い"],
                       [[[20, 120, 100], [35, 255, 255]]]),
        ]
        return cfg

    # ------------------------------------------------------------------- I/O
    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> AppConfig:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = AppConfig(
            camera=CameraConfig(**d.get("camera", {})),
            robot=RobotConfig(**{**d.get("robot", {}),
                                 "home_xyzr": tuple(d.get("robot", {}).get("home_xyzr", RobotConfig().home_xyzr))}),
            llm=LLMConfig(**d.get("llm", {})),
            asr=ASRConfig(**d.get("asr", {})),
            tts=TTSConfig(**d.get("tts", {})),
            zones=[Zone(**z) for z in d.get("zones", [])],
            colors=[ColorRange(**c) for c in d.get("colors", [])],
        )
        for k in ("homography_path", "min_area_px", "max_area_px", "log_dir", "panel_window"):
            if k in d:
                setattr(cfg, k, d[k])
        return cfg

    # --------------------------------------------------------------- helpers
    def zone_by_name(self, name: str) -> Zone | None:
        for z in self.zones:
            if z.name == name:
                return z
        return None

    def zone_by_alias(self, text: str) -> Zone | None:
        """発話文の中に別名が含まれていれば、そのゾーン（長い別名を優先）。"""
        best: tuple[int, Zone] | None = None
        for z in self.zones:
            for a in [z.name] + z.aliases:
                if a and a in text and (best is None or len(a) > best[0]):
                    best = (len(a), z)
        return best[1] if best else None

    def color_by_alias(self, text: str) -> ColorRange | None:
        best: tuple[int, ColorRange] | None = None
        for c in self.colors:
            for a in [c.name] + c.aliases:
                if a and a in text and (best is None or len(a) > best[0]):
                    best = (len(a), c)
        return best[1] if best else None

    def color_names(self) -> list[str]:
        return [c.name for c in self.colors]

    def zone_names(self) -> list[str]:
        return [z.name for z in self.zones]


if __name__ == "__main__":  # `python config.py path.json` で既定設定を書き出す
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    AppConfig.default().save(out)
    print("wrote", out)
