"""LLM（Claude）による意図解釈 → ツール呼び出し → TaskExecutor 実行。

設計（claude-api skill 準拠・anthropic SDK 1.x・Messages API）
- ツールは 5 つだけ: list_objects / pick_and_place / tidy_up / go_home / stop。**座標を受け取るツールは無い**。
  引数は enum（対象物名・ゾーン名・選び方ヒント）で `strict: True`。LLM が勝手な値を作れない。
- モデル既定 `claude-opus-5`（skill 規約）、adaptive thinking、effort=low（展示は応答速度優先）。
  応答速度を最優先するなら `claude-haiku-4-5` をユーザー判断で（LLMConfig.model）。
- stop_reason == "refusal" は `stop_details` を見て日本語で言い直しを促す（例外にしない）。
- 会話履歴は直近 N 往復だけ保持（「もう一個」「それを左に」に対応）。
- ネット断・鍵なし・例外時は rule_parser にフォールバック（main/demo が制御）。
"""
from __future__ import annotations

import json
import time
from typing import Any

from config import AppConfig
from planner import Result, TaskExecutor

SYSTEM_PROMPT_JA = """あなたは展示ブースの小型ロボットアーム（Dobot Magician）の受付係です。来場者の日本語の発話を聞き、
用意されたツールだけでロボットを動かします。ルール:
1. 物を動かす前に必要なら list_objects で今見えている物を確認する（毎回は不要。指示が明確なら直接 pick_and_place でよい）。
2. 対象物は {objects} のどれか、置き場は {zones} のどれかに必ず対応づける。曖昧な言い方（「赤いやつ」「たま」）は最も近い対象物に寄せてよいが、
   対応づけられない物や場所を言われたら、動かさずに短く聞き返す。
3. 置き場が言われていないときは動かさずに聞き返す（例:「右と左、どちらに置きますか？」）。
4. 1回の発話で動かすのは原則1個。「全部」と言われたときだけ count=-1。「片付けて」「元に戻して」は tidy_up。
5. 返答は話し言葉の日本語で1〜2文、丁寧で短く。ツールの結果（message）をそのまま伝えてよい。
6. 危険な指示、ロボット以外の依頼、個人情報の要求には応じない。
"""

TOOLS_TEMPLATE: list[dict[str, Any]] = [
    {
        "name": "list_objects",
        "description": "カメラで今見えている物の一覧（種類・個数・位置）を返す。",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "pick_and_place",
        "description": "指定した物を1個（count=-1なら全部）拾って、指定の置き場に置く。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "object": {"type": "string", "enum": ["__OBJECTS__"], "description": "拾う物"},
                "zone": {"type": "string", "enum": ["__ZONES__"], "description": "置き場"},
                "hint": {"type": "string", "enum": ["any", "largest", "smallest", "nearest", "leftmost", "rightmost"],
                         "description": "同じ物が複数あるときの選び方"},
                "count": {"type": "integer", "enum": [1, -1], "description": "1=1個, -1=全部"},
            },
            "required": ["object", "zone", "hint", "count"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tidy_up",
        "description": "見えている物を全部スタート台（元の場所）に戻す。「片付けて」「元に戻して」「リセット」のとき。",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "go_home",
        "description": "ロボットをホーム位置（待機姿勢）に戻す。",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "stop",
        "description": "ロボットを直ちに止める（来場者が『止まって』と言ったとき）。",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
]


def build_tools(cfg: AppConfig) -> list[dict[str, Any]]:
    tools = json.loads(json.dumps(TOOLS_TEMPLATE))
    for t in tools:
        if t["name"] == "pick_and_place":
            t["input_schema"]["properties"]["object"]["enum"] = cfg.object_names()
            t["input_schema"]["properties"]["zone"]["enum"] = cfg.zone_names()
    return tools


def build_system(cfg: AppConfig) -> str:
    objects = "、".join(f"{o.name}({o.label})" for o in cfg.objects)
    zones = "、".join(f"{z.name}({z.aliases[0]})" for z in cfg.zones)
    return SYSTEM_PROMPT_JA.format(objects=objects, zones=zones)


class ClaudeAgent:
    def __init__(self, cfg: AppConfig, executor: TaskExecutor, logger=None):
        import anthropic  # 遅延 import（未導入でもルールベース経路は動く）
        self.anthropic = anthropic
        self.cfg = cfg
        self.executor = executor
        self.client = anthropic.Anthropic(timeout=cfg.llm.timeout_s, max_retries=1)
        self.tools = build_tools(cfg)
        self.system = [{"type": "text", "text": build_system(cfg), "cache_control": {"type": "ephemeral"}}]
        self.history: list[dict] = []
        self.logger = logger or (lambda ev: None)

    # ---------------------------------------------------------------- tools
    def _run_tool(self, name: str, inp: dict) -> Result:
        ex = self.executor
        if name == "list_objects":
            return ex.list_objects()
        if name == "pick_and_place":
            return ex.pick_and_place(inp.get("object"), inp.get("zone"), inp.get("hint", "any"), int(inp.get("count", 1)))
        if name == "tidy_up":
            return ex.tidy_up()
        if name == "go_home":
            return ex.go_home()
        if name == "stop":
            return ex.stop()
        return Result(False, f"unknown tool {name}")

    # ------------------------------------------------------------------ run
    def handle(self, utterance: str) -> str:
        """発話 → （ツール呼び出しループ）→ 返答テキスト。"""
        llm = self.cfg.llm
        messages = self._trim(self.history) + [{"role": "user", "content": utterance}]
        t0 = time.time()
        final_text = ""
        for turn in range(llm.max_turns):
            resp = self.client.messages.create(
                model=llm.model,
                max_tokens=llm.max_tokens,
                system=self.system,
                tools=self.tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": llm.effort},
            )
            self.logger({"ev": "llm", "turn": turn, "stop": resp.stop_reason,
                         "in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
                         "cache_read": getattr(resp.usage, "cache_read_input_tokens", None),
                         "ms": int((time.time() - t0) * 1000)})
            if resp.stop_reason == "refusal":
                cat = getattr(getattr(resp, "stop_details", None), "category", None)
                final_text = "その指示にはお応えできません。物の名前と置き場を言ってください。"
                self.logger({"ev": "refusal", "category": cat})
                messages.append({"role": "assistant", "content": final_text})
                break
            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            texts = [b.text for b in resp.content if b.type == "text"]
            if not tool_uses:
                final_text = " ".join(texts).strip()
                break
            results = []
            for tu in tool_uses:
                inp = tu.input if isinstance(tu.input, dict) else json.loads(json.dumps(tu.input))
                self.logger({"ev": "tool_call", "name": tu.name, "input": inp})
                r = self._run_tool(tu.name, inp)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": r.to_json(),
                                "is_error": not r.ok})
            messages.append({"role": "user", "content": results})  # 並列呼び出しは 1 メッセージにまとめて返す
        else:
            final_text = final_text or "処理が長くなったので一旦止めました。もう一度お願いします。"
        self.history = self._trim(messages)
        return final_text or "はい。"

    def _trim(self, messages: list[dict]) -> list[dict]:
        """直近 history_turns 往復に切り詰める（先頭は必ず user、tool_result 対の途中で切らない）。"""
        keep = self.cfg.llm.history_turns * 2
        msgs = messages[-keep:] if keep > 0 else []
        while msgs and (msgs[0]["role"] != "user" or _is_tool_result_msg(msgs[0])):
            msgs = msgs[1:]
        return msgs


def _is_tool_result_msg(m: dict) -> bool:
    c = m.get("content")
    return isinstance(c, list) and bool(c) and isinstance(c[0], dict) and c[0].get("type") == "tool_result"


class RuleAgent:
    """LLM 不使用の同一インターフェース（オフライン退避）。"""
    def __init__(self, cfg: AppConfig, executor: TaskExecutor, logger=None):
        from rule_parser import parse
        self.parse = parse
        self.cfg = cfg
        self.executor = executor
        self.logger = logger or (lambda ev: None)
        self.last_object: str | None = None

    def handle(self, utterance: str) -> str:
        it = self.parse(utterance, self.cfg)
        self.logger({"ev": "rule_intent", **it.to_dict()})
        ex = self.executor
        if it.action == "stop":
            return ex.stop().message
        if it.action == "tidy":
            return ex.tidy_up().message
        if it.action == "home":
            return ex.go_home().message
        if it.action == "list":
            return ex.list_objects().message
        if it.action == "pick_and_place":
            obj = it.object or (None if it.count == -1 else self.last_object)
            if obj is None and it.count != -1:
                names = "・".join(o.label for o in self.cfg.objects[:4])
                return f"どれを動かしますか？（{names} など）"
            if it.zone is None:
                names = "・".join(z.aliases[0] for z in self.cfg.zones if z.aliases)
                return f"どこに置きますか？（{names}）"
            r = ex.pick_and_place(obj, it.zone, it.hint, it.count)
            if r.ok and obj:
                self.last_object = obj
            return r.message
        return "すみません、聞き取れませんでした。「赤いブロックを右のトレイに置いて」のように言ってください。"


def make_agent(cfg: AppConfig, executor: TaskExecutor, logger=None):
    """LLM が使えれば ClaudeAgent、無理なら RuleAgent（理由をログに残す）。"""
    if cfg.llm.enabled and cfg.llm.provider == "anthropic":
        try:
            return ClaudeAgent(cfg, executor, logger)
        except Exception as e:  # SDK 未導入・鍵なし等
            (logger or print)({"ev": "llm_unavailable", "reason": repr(e)})
    return RuleAgent(cfg, executor, logger)
