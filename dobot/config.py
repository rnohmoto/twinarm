"""dobot 設定（JSON 永続化・依存ゼロ）。

設計方針
- 「LLM に座標を作らせない」: ロボット座標系で意味を持つのは、この設定に登録した
  ゾーン（置き場）と、キャリブレーション（ホモグラフィ）で画像から変換した検出結果だけ。
- 単位: ロボット座標は mm（Dobot Magician のベース座標系）。画像はピクセル。
- 対象物は「色（HSV）＋形（円形度・縦横比）＋面積」で識別する（v2・2026-09-20）。
  既定は 色つき立方体 4 色・卓球ボール（橙）・MONO 消しゴム（青帯）・白いボール（ゴルフ）。
  実機で必ず `setup_wizard.py`（または calibrate.py と main.py --tune）で上書きする。
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
    port: str | None = None          # None=未指定（推測しない。--port か setup_wizard で書く）
    end_effector: str = "suction"    # suction | gripper
    ee_settle_s: float = 0.6         # 吸着/把持が効くまでの待ち
    z_safe: float = 40.0             # 移動時の退避高さ（テーブル面基準ではなく座標値）
    z_pick: float = -30.0            # 物体上面に吸盤が当たる高さ（既定。物ごとの値は ObjectSpec.z_pick）
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
    radius_mm: float = 60.0          # この半径内の物は「もうこのゾーンにある」とみなす（片付け・重複置きの防止）
    slots: list[list[float]] = field(default_factory=list)  # 置く位置の候補 [[x,y],...]。空なら (x,y) に置く


@dataclass
class ObjectSpec:
    """対象物 1 種。HSV 範囲＋形の条件で識別する。"""
    name: str                        # 英字の識別名（LLM の enum・ログ）
    label: str                       # 日本語の呼び名（返事・パネル）
    aliases: list[str]               # 発話で拾う語（長い一致を優先）
    ranges: list[list[list[int]]]    # [[[h_lo,s_lo,v_lo],[h_hi,s_hi,v_hi]], ...]（白は S 低・V 高の範囲で書く）
    min_area_px: int | None = None   # None → AppConfig.min_area_px
    max_area_px: int | None = None
    min_circularity: float = 0.0     # 4πA/P² の下限（正方形 ≈0.79・円 ≈1.0）。球は 0.82 以上
    aspect_min: float = 1.0          # 外接矩形の 長辺/短辺 の範囲（細長い物の識別）
    aspect_max: float = 99.0
    z_pick: float | None = None      # None → RobotConfig.z_pick（物の高さが違うので原則は物ごとに教える）
    ee: str = "suction"              # suction | gripper（記録用）


@dataclass
class DemoConfig:
    """実演の運転（demo.py）。既定は「自動では回さない・回すなら短く・熱の予算内」。"""
    attract: bool = False            # 誰も話さない間、自動で「運ぶ→片付ける」を繰り返す（既定 OFF）
    idle_s: float = 120.0            # 最後の操作からこの秒数で自動ループを始める
    pause_s: float = 45.0            # 自動ループ 1 サイクル後の休み（モーター発熱の抑制）
    attract_zone: str = "tray_right" # 自動ループで運ぶ先
    start_zone: str = "start"        # 片付け先（スタート台）
    attract_count: int = 1           # 自動ループ 1 サイクルで運ぶ個数（1 個運んで戻す＝約 20 秒で終わる）
    loop_cycles: int = 3             # 「ループして」の既定回数（回数が終わったら止まる）
    max_cycles_per_hour: int = 20    # 自動ループ・「ループして」の合計上限（発熱・摩耗）
    motion_budget_s: float = 240.0   # 直近 budget_window_s 秒のうち腕を動かしてよい秒数（≈40%・ステッパの発熱の目安）
    budget_window_s: float = 600.0
    cooldown_s: float = 120.0        # 予算を使い切ったら最低この秒数は自動動作を止める（対話は説明員の判断で可）
    panel_port: int = 8790           # ブラウザ UI（http://127.0.0.1:8790）
    stream_fps: int = 8
    jpeg_quality: int = 70


@dataclass
class LLMConfig:
    enabled: bool = True
    provider: str = "anthropic"      # anthropic | none（none=ルールベース解析のみ）
    model: str = "claude-sonnet-5"   # ユーザー裁定 2026-09-20「Sonnet でよい」。品質優先なら claude-opus-5（1 行で戻せる）
    effort: str = "low"              # low | medium | high（展示は応答速度優先で low。adaptive thinking と併用）
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
    demo: DemoConfig = field(default_factory=DemoConfig)
    zones: list[Zone] = field(default_factory=list)
    objects: list[ObjectSpec] = field(default_factory=list)
    homography_path: str = "assets/homography.json"
    min_area_px: int = 400           # 検出の最小面積（1280x720・高さ45cm・25mm角で概ね 2500〜4500px）
    max_area_px: int = 60000
    log_dir: str = "logs"
    panel_window: bool = True        # OpenCV ウィンドウでパネル表示（main.py）

    # ---------------------------------------------------------------- defaults
    @staticmethod
    def default() -> AppConfig:
        cfg = AppConfig()
        cfg.zones = [
            Zone("tray_right", ["右", "みぎ", "右のトレイ", "右側", "右のお皿", "right"], 230.0, -110.0,
                 slots=[[215.0, -125.0], [245.0, -125.0], [215.0, -95.0], [245.0, -95.0]]),
            Zone("tray_left", ["左", "ひだり", "左のトレイ", "左側", "左のお皿", "left"], 230.0, 110.0,
                 slots=[[215.0, 95.0], [245.0, 95.0], [215.0, 125.0], [245.0, 125.0]]),
            Zone("box_front", ["手前", "てまえ", "前", "手前の箱", "front"], 170.0, 0.0),
            Zone("box_back", ["奥", "おく", "後ろ", "奥の箱", "back"], 280.0, 0.0),
            Zone("start", ["スタート", "元の場所", "もとの場所", "台", "start"], 235.0, 0.0, radius_mm=75.0,
                 slots=[[210.0, -40.0], [210.0, 0.0], [210.0, 40.0], [260.0, -40.0], [260.0, 0.0], [260.0, 40.0]]),
        ]
        cube = {"min_circularity": 0.0, "aspect_min": 1.0, "aspect_max": 1.6}
        cfg.objects = [
            ObjectSpec("red_cube", "赤いブロック", ["赤", "あか", "レッド", "赤い", "赤色"],
                       [[[0, 120, 70], [8, 255, 255]], [[170, 120, 70], [179, 255, 255]]], **cube),
            ObjectSpec("green_cube", "緑のブロック", ["緑", "みどり", "グリーン", "緑の", "緑色", "青緑"],
                       [[[40, 80, 60], [85, 255, 255]]], **cube),
            ObjectSpec("blue_cube", "青いブロック", ["青", "あお", "ブルー", "青い", "青色", "水色"],
                       [[[95, 120, 60], [130, 255, 255]]], **cube),
            ObjectSpec("yellow_cube", "黄色いブロック", ["黄", "きいろ", "黄色", "イエロー", "黄色い"],
                       [[[20, 120, 100], [35, 255, 255]]], **cube),
            # 卓球ボール（橙・φ40mm・2.7g）: 橙の色相＋円形度で立方体と区別する
            ObjectSpec("ball", "ボール", ["ボール", "ぼーる", "たま", "球", "ピンポン", "卓球", "橙", "オレンジ"],
                       [[[8, 120, 120], [22, 255, 255]]], min_circularity=0.82, aspect_min=1.0, aspect_max=1.25),
            # MONO 消しゴム: スリーブの青帯（細長い）。吸盤は帯の中心に来るので実機で pick 位置を確認（M0.5）
            ObjectSpec("eraser", "消しゴム", ["消しゴム", "けしごむ", "ケシゴム", "MONO", "モノ"],
                       [[[95, 120, 60], [130, 255, 255]]], aspect_min=2.2, aspect_max=8.0, ee="gripper"),
            # 白いボール（ゴルフ）: S 低・V 高＋円形度。ArUco の白地（正方形 ≈0.79）を円形度で弾く
            ObjectSpec("golf_ball", "ゴルフボール", ["ゴルフ", "ごるふ", "白いボール", "白い球"],
                       [[[0, 0, 170], [179, 70, 255]]], min_circularity=0.85, aspect_min=1.0, aspect_max=1.2),
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
            demo=DemoConfig(**d.get("demo", {})),
            zones=[Zone(**z) for z in d.get("zones", [])],
            objects=[ObjectSpec(**o) for o in d.get("objects", [])],
        )
        if not cfg.objects and d.get("colors"):  # v1 の config（colors）を読めるようにする
            cfg.objects = [ObjectSpec(c["name"], c["aliases"][0] if c.get("aliases") else c["name"],
                                      c.get("aliases", []), c["ranges"]) for c in d["colors"]]
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

    def object_by_name(self, name: str) -> ObjectSpec | None:
        for o in self.objects:
            if o.name == name:
                return o
        return None

    def object_by_alias(self, text: str) -> ObjectSpec | None:
        best: tuple[int, ObjectSpec] | None = None
        for o in self.objects:
            for a in [o.name, o.label] + o.aliases:
                if a and a in text and (best is None or len(a) > best[0]):
                    best = (len(a), o)
        return best[1] if best else None

    def object_names(self) -> list[str]:
        return [o.name for o in self.objects]

    def label(self, name: str | None) -> str:
        o = self.object_by_name(name) if name else None
        return o.label if o else (name or "それ")

    def zone_names(self) -> list[str]:
        return [z.name for z in self.zones]

    def zone_label(self, name: str) -> str:
        z = self.zone_by_name(name)
        return (z.aliases[0] if z and z.aliases else name)


if __name__ == "__main__":  # `python config.py path.json` で既定設定を書き出す
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    AppConfig.default().save(out)
    print("wrote", out)
