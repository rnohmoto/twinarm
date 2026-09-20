"""ルールベースの日本語意図解析（LLM 不要・オフライン退避／LLM 前段の正規化）。

対応する発話の型
  「赤いブロックを右のトレイに置いて」「みどり拾って左」「ボールを奥に移して」「全部手前に入れて」
  「一番大きい赤を右」「ホームに戻って」「止まって」「何がある？」「片付けて」「元に戻して」
返り値 Intent: action ∈ {pick_and_place, list, home, stop, tidy, unknown}
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from config import AppConfig


@dataclass
class Intent:
    action: str
    object: str | None = None    # ObjectSpec.name
    zone: str | None = None      # Zone.name
    count: int = 1                  # 1 or -1（全部）
    hint: str = "any"               # largest | smallest | nearest | leftmost | rightmost | any
    raw: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


_STOP = ("止まって", "とまって", "ストップ", "stop", "やめて", "中止")
_TIDY = ("片付け", "かたづけ", "戻して", "もどして", "リセット", "元に戻", "もとに戻", "元の場所", "もとの場所")
_HOME = ("ホーム", "戻って", "もどって", "初期位置", "home")
_LIST = ("何がある", "なにがある", "何が見える", "なにが見える", "見えてる", "リスト", "一覧", "何個", "なんこ")
_PICK_VERBS = ("拾", "ひろ", "取", "とって", "つか", "掴", "持", "運", "移", "置", "おいて", "入れ", "いれ", "動か", "うごか")
_HINTS = (
    ("一番大きい", "largest"), ("いちばん大きい", "largest"), ("大きい方", "largest"), ("大きいほう", "largest"),
    ("一番小さい", "smallest"), ("いちばん小さい", "smallest"), ("小さい方", "smallest"), ("小さいほう", "smallest"),
    ("一番近い", "nearest"), ("いちばん近い", "nearest"), ("近い方", "nearest"),
    ("一番左", "leftmost"), ("いちばん左", "leftmost"), ("左の方の", "leftmost"),
    ("一番右", "rightmost"), ("いちばん右", "rightmost"), ("右の方の", "rightmost"),
)


def normalize(text: str) -> str:
    t = text.strip()
    t = re.sub(r"[\s、。！!？?・〜~「」『』]", "", t)   # 「ー」は残す（ボール・グリーン等の別名に含まれる）
    return t


def parse(text: str, cfg: AppConfig) -> Intent:
    raw = text
    t = normalize(text)
    if any(k in t for k in _STOP):
        return Intent("stop", raw=raw)
    if any(k in t for k in _TIDY):
        return Intent("tidy", raw=raw)
    if any(k in t for k in _HOME) and not any(v in t for v in ("置", "おいて", "入れ")):
        return Intent("home", raw=raw)
    if any(k in t for k in _LIST):
        return Intent("list", raw=raw)

    hint = "any"
    for k, h in _HINTS:
        if k in t:
            hint = h
            t = t.replace(k, "")
            break
    count = -1 if any(k in t for k in ("全部", "ぜんぶ", "すべて", "全て", "みんな")) else 1

    obj = cfg.object_by_alias(t)
    # 目的地: 「一番左」を hint に食わせた後の文で探す。「左のトレイに」の「に/へ」があると強い
    zone = None
    m = re.search(r"(.+?)(に|へ|の中に|の上に)(置|おい|入れ|いれ|移|うつ|運|はこ|持って|もって)", t)
    if m:
        zone = cfg.zone_by_alias(m.group(1))
    if zone is None:
        zone = cfg.zone_by_alias(t)
    is_pick = any(v in t for v in _PICK_VERBS) or (obj is not None and zone is not None)
    if is_pick and obj is not None:
        return Intent("pick_and_place", obj.name, zone.name if zone else None, count, hint, raw)
    if is_pick and obj is None and zone is not None:
        # 対象指定なし: 「それを右に」「全部手前に」等 → 呼び出し側で直前の対象 or 全部に解決
        return Intent("pick_and_place", None, zone.name, count, hint, raw)
    return Intent("unknown", raw=raw)


if __name__ == "__main__":
    cfg = AppConfig.default()
    for s in ["赤いブロックを右のトレイに置いて", "みどり拾って左", "ボールを奥に移して", "全部手前に入れて",
              "一番大きい赤を右", "ホームに戻って", "止まって", "何がある？", "黄色いのを取って", "片付けて", "消しゴムを左に"]:
        print(s, "->", parse(s, cfg))
