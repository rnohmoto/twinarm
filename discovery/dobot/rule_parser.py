"""ルールベースの日本語意図解析（LLM 不要・オフライン退避／LLM 前段の正規化）。

対応する発話の型
  「赤いブロックを右のトレイに置いて」「みどり拾って左」「ボールを奥に移して」「全部手前に入れて」
  「一番大きい赤を右」「ホームに戻って」「止まって」「何がある？」「片付けて」「元に戻して」
返り値 Intent: action ∈ {pick_and_place, list, home, stop, tidy, loop, unsupported, unknown}

曖昧さの検出（robotics T13 Step 3・113_ §3.7・114_ §4.2・裁定 ②）
  否定・訂正（「赤じゃなくて青」）・順序（「〜てから」）・複数の対象／置き場・直前を指す語（「さっきの」）・「リセット」は、
  最もありそうな解釈を Intent に入れたうえで ambiguous=True と reason を付ける。声の UI（voice_front）は ambiguous なら
  「これですか？」と確認してから動かす。従来の呼び出し（RuleAgent.handle＝文字入力）はそのまま実行する。
  否定のときは後に言った物・置き場を採る（「赤じゃなくて青を右に」→ 青）。交換・除外（「〜以外」）はまだカタログに無いので
  unsupported。停止語は最優先で、複文でも stop。「ホーム」を含む「戻して」は片付けでなくホーム。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields

from config import AppConfig


@dataclass
class Intent:
    action: str
    object: str | None = None    # ObjectSpec.name
    zone: str | None = None      # Zone.name
    count: int = 1                  # 1 or -1（全部）
    hint: str = "any"               # largest | smallest | nearest | leftmost | rightmost | any
    raw: str = ""
    ambiguous: bool = False         # True なら実行前に「これですか？」と確認する（声の UI）
    reason: str = ""                # negation | sequence | reference | objects | zones | reset | swap | exclusion

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> Intent:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


_STOP = ("止まって", "とまって", "ストップ", "stop", "やめて", "中止",
         "止めて", "とめて", "止まれ", "とまれ", "待って", "まって", "危ない", "あぶない", "だめ", "ダメ")
_LOOP = ("ループ", "るーぷ", "繰り返", "くりかえ", "回して", "まわして", "デモして", "自動で")
_TIDY = ("片付け", "かたづけ", "戻して", "もどして", "リセット", "元に戻", "もとに戻", "元の場所", "もとの場所")
_HOME_NOUN = ("ホーム", "原点", "初期位置", "home")
_NUM = {"一": 1, "１": 1, "二": 2, "２": 2, "三": 3, "３": 3, "四": 4, "４": 4, "五": 5, "５": 5}
# 曖昧さの印（文字列の一致。形態素解析は使わない）
_NEG = ("じゃなくて", "ではなく", "じゃない", "ではない", "以外", "いや", "やっぱり", "違う", "ちがう",
        "でなく", "ないで", "なしで", "除いて", "抜きで")
_SEQ = ("てから", "たら", "次に", "その後", "あとで", "それから", "そして", "両方", "それぞれ")
_SWAP = ("交換", "入れ替", "いれかえ", "入替")
_EXCL = ("以外", "除いて", "抜きで", "なしで")
_DEIXIS = ("さっき", "もう一", "もう1", "もう１", "同じ", "今の", "次は", "それを", "あれを", "これを")


def loop_count(t: str, default: int) -> int:
    """「3回」「三回」「２回」→ 回数。無ければ default。1〜5 に丸める。"""
    m = re.search(r"([0-9０-９]+|[一二三四五])回", t)
    if not m:
        return default
    s = m.group(1)
    n = _NUM.get(s, int(s) if s.isdigit() else default)   # int() は全角数字も読める
    return max(1, min(5, n))
_HOME = ("ホーム", "戻って", "もどって", "初期位置", "原点", "home")
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


def has_negation(t: str) -> bool:
    """否定・訂正の印。「赤いやつ」の「いや」は数えない。"""
    for k in _NEG:
        if k == "いや":
            if "いや" in t and "いやつ" not in t:
                return True
        elif k in t:
            return True
    return False


def mentions(t: str, cfg: AppConfig) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """最長一致で消費しながら、物体・置き場の言及を (位置, 名前) の昇順で返す（「ゴルフボール」を 2 物体に数えない）。"""
    cand: list[tuple[str, str, str]] = []
    for o in cfg.objects:
        for al in {o.label, *o.aliases}:
            if al:
                cand.append((al, "o", o.name))
    for z in cfg.zones:
        for al in z.aliases:
            if al:
                cand.append((al, "z", z.name))
    cand.sort(key=lambda c: -len(c[0]))
    s = t
    objs: list[tuple[int, str]] = []
    zones: list[tuple[int, str]] = []
    for al, kind, name in cand:
        start = 0
        while True:
            i = s.find(al, start)
            if i < 0:
                break
            (objs if kind == "o" else zones).append((i, name))
            s = s[:i] + "\0" * len(al) + s[i + len(al):]
            start = i + len(al)
    return sorted(objs), sorted(zones)


def _flag(it: Intent, t: str) -> Intent:
    """home／loop／tidy に、否定・順序・直前参照の印があれば ambiguous を付ける。"""
    reasons = []
    if has_negation(t):
        reasons.append("negation")
    if any(k in t for k in _SEQ):
        reasons.append("sequence")
    if any(k in t for k in _DEIXIS):
        reasons.append("reference")
    if reasons:
        it.ambiguous = True
        it.reason = "+".join([it.reason] * bool(it.reason) + reasons)
    return it


def parse(text: str, cfg: AppConfig) -> Intent:
    raw = text
    t = normalize(text)
    tl = t.lower()
    if any(k in tl for k in _STOP):
        return Intent("stop", raw=raw)
    not_place = not any(v in t for v in ("置", "おいて", "入れ"))
    if any(k in tl for k in _HOME_NOUN) and not_place:
        return _flag(Intent("home", raw=raw), t)              # 「ホームに戻して」は片付けより先に判定
    if any(k in t for k in _LOOP) and not any(k in t for k in _TIDY):
        return _flag(Intent("loop", count=loop_count(t, cfg.demo.loop_cycles), raw=raw), t)
    if any(k in t for k in _SWAP):
        return Intent("unsupported", raw=raw, ambiguous=True, reason="swap")
    if any(k in t for k in _TIDY):
        it = Intent("tidy", raw=raw)
        if "リセット" in t:
            it.ambiguous, it.reason = True, "reset"             # ロボットの復旧の意味かもしれない
        return _flag(it, t)
    if any(k in tl for k in _HOME) and not_place:
        return _flag(Intent("home", raw=raw), t)
    if any(k in t for k in _LIST):
        return Intent("list", raw=raw)

    hint = "any"
    for k, h in _HINTS:
        if k in t:
            hint = h
            t = t.replace(k, "")
            break
    count = -1 if any(k in t for k in ("全部", "ぜんぶ", "すべて", "全て", "みんな")) else 1
    if any(k in t for k in _EXCL):
        return Intent("unsupported", raw=raw, ambiguous=True, reason="exclusion")

    obj = cfg.object_by_alias(t)
    # 目的地: 「一番左」を hint に食わせた後の文で探す。「左のトレイに」の「に/へ」があると強い
    zone = None
    m = re.search(r"(.+?)(に|へ|の中に|の上に)(置|おい|入れ|いれ|移|うつ|運|はこ|持って|もって)", t)
    if m:
        zone = cfg.zone_by_alias(m.group(1))
    if zone is None:
        zone = cfg.zone_by_alias(t)

    objs, zones = mentions(t, cfg)
    obj_names = {n for _, n in objs}
    zone_names = {n for _, n in zones}
    neg = has_negation(t)
    if neg and len(obj_names) >= 2:                     # 「赤じゃなくて青」→ 後に言った方
        obj = cfg.object_by_name(objs[-1][1])
    if neg and len(zone_names) >= 2:                    # 「右、いや左」→ 後に言った方
        zone = cfg.zone_by_name(zones[-1][1])
    deixis = any(k in t for k in _DEIXIS)
    # 動詞が無くても、物が言われていれば拾う意図とみなす（置き場が無ければ呼び出し側が「どこに置きますか？」と聞き返す）
    is_pick = (any(v in t for v in _PICK_VERBS) or obj is not None or (deixis and zone is not None))
    if is_pick and obj is not None:
        it = Intent("pick_and_place", obj.name, zone.name if zone else None, count, hint, raw)
    elif is_pick and obj is None and zone is not None:
        # 対象指定なし: 「それを右に」「全部手前に」等 → 呼び出し側で直前の対象 or 全部に解決
        it = Intent("pick_and_place", None, zone.name, count, hint, raw)
    else:
        return Intent("unknown", raw=raw)
    reasons = []
    if neg:
        reasons.append("negation")
    if any(k in t for k in _SEQ):
        reasons.append("sequence")
    if deixis or (it.object is None and it.count != -1):
        reasons.append("reference")
    if len(obj_names) >= 2:
        reasons.append("objects")
    if len(zone_names) >= 2:
        reasons.append("zones")
    if reasons:
        it.ambiguous, it.reason = True, "+".join(reasons)
    return it


if __name__ == "__main__":
    cfg = AppConfig.default()
    for s in ["赤いブロックを右のトレイに置いて", "みどり拾って左", "ボールを奥に移して", "全部手前に入れて",
              "一番大きい赤を右", "ホームに戻って", "止まって", "何がある？", "黄色いのを取って", "片付けて", "消しゴムを左に",
              "赤じゃなくて青を右に", "赤を右、いや左に置いて", "ホームに戻して", "リセットして", "青取って赤取って交換して"]:
        print(s, "->", parse(s, cfg))
