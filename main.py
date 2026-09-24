from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .src.engine import GamePhase, UndercoverError, UndercoverGame
    from .src.qqofficial import (
        ButtonSpec,
        extract_context,
        is_qqofficial_event,
        send_group_reply,
    )
except ImportError:  # pragma: no cover - direct local import fallback
    from src.engine import GamePhase, UndercoverError, UndercoverGame
    from src.qqofficial import (
        ButtonSpec,
        extract_context,
        is_qqofficial_event,
        send_group_reply,
    )


PLUGIN_NAME = "astrbot_plugin_undercover"


@dataclass
class CommandOutcome:
    """Result of one Undercover command."""

    text: str
    game: UndercoverGame | None = None
    buttons: list[ButtonSpec] | None = None
    error: bool = False


@register(
    PLUGIN_NAME,
    "Codex",
    "QQ 官方群聊谁是卧底：按钮看词、发言、投票和结算。",
    "1.0.0",
)
class UndercoverPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = dict(config) if config else {}
        self.max_players = self._config_int("max_players", 10, minimum=4, maximum=12)
        self.undercover_count = self._config_int("undercover_count", 1, minimum=1)
        self.blank_count = self._config_int("blank_count", 0, minimum=0, maximum=2)
        self.games: dict[str, UndercoverGame] = {}
        self.group_locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """Initialize the plugin."""

        logger.info("[Undercover] initialized")

    async def terminate(self) -> None:
        """Drop all in-memory games on plugin unload."""

        self.games.clear()
        self.group_locks.clear()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @filter.command("卧底菜单", alias={"卧底帮助", "谁是卧底"})
    async def menu_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "menu"):
            yield result
        event.stop_event()

    @filter.command("卧底创建", alias={"卧底开局", "卧底开房"})
    async def create_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "create"):
            yield result
        event.stop_event()

    @filter.command("卧底加入", alias={"卧底报名"})
    async def join_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "join"):
            yield result
        event.stop_event()

    @filter.command("卧底退出", alias={"卧底离开"})
    async def leave_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "leave"):
            yield result
        event.stop_event()

    @filter.command("卧底开始", alias={"卧底发词"})
    async def start_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "start"):
            yield result
        event.stop_event()

    @filter.command("卧底看", alias={"卧底状态"})
    async def status_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "status"):
            yield result
        event.stop_event()

    @filter.command("我的词语", alias={"查看词语", "卧底看词"})
    async def word_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "word"):
            yield result
        event.stop_event()

    @filter.command("开始投票", alias={"卧底投票"})
    async def start_vote_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "start_vote"):
            yield result
        event.stop_event()

    @filter.command("投票", alias={"卧底投"})
    async def vote_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "vote"):
            yield result
        event.stop_event()

    @filter.command("统计投票", alias={"卧底开票"})
    async def tally_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "tally"):
            yield result
        event.stop_event()

    @filter.command("卧底结束", alias={"卧底取消"})
    async def end_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "end"):
            yield result
        event.stop_event()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    async def _handle_command(self, event: AstrMessageEvent, command: str):
        group_id, user_id, name = self._identity(event)
        if not group_id:
            yield event.plain_result("谁是卧底只能在群聊中使用。")
            return
        lock = self.group_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            try:
                outcome = await self._execute_command(
                    event, group_id, user_id, name, command
                )
            except UndercoverError as exc:
                outcome = CommandOutcome(text=str(exc), error=True)
            except Exception as exc:  # noqa: BLE001 - isolate one group
                logger.exception("[Undercover] command %s failed: %s", command, exc)
                outcome = CommandOutcome(text=f"谁是卧底处理失败：{exc}", error=True)
        if outcome.error:
            yield event.plain_result(outcome.text)
            return
        buttons = outcome.buttons
        if buttons is None and outcome.game is not None:
            buttons = self._game_buttons(outcome.game)
        if await self._try_send_qqofficial(event, outcome.text, buttons):
            return
        if outcome.text:
            yield event.plain_result(outcome.text)

    async def _execute_command(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
        name: str,
        command: str,
    ) -> CommandOutcome:
        if command == "menu":
            return self._menu_outcome()
        if command == "create":
            return self._create_game(group_id, user_id, name)
        if command == "join":
            return self._join_game(group_id, user_id, name)
        if command == "leave":
            return self._leave_game(group_id, user_id)
        if command == "start":
            return self._start_game(event, group_id, user_id)
        if command == "status":
            return self._show_status(group_id)
        if command == "word":
            return self._show_word(group_id)
        if command == "start_vote":
            return self._start_vote(group_id, user_id)
        if command == "vote":
            return self._vote(group_id, user_id, self._message_text(event))
        if command == "tally":
            return self._tally(group_id, user_id)
        if command == "end":
            return self._end_game(event, group_id, user_id)
        raise UndercoverError("未知指令。")

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------
    def _create_game(self, group_id: str, user_id: str, name: str) -> CommandOutcome:
        existing = self.games.get(group_id)
        if existing is not None and existing.phase != GamePhase.FINISHED:
            raise UndercoverError("本群已经有一局谁是卧底正在进行。")
        game = UndercoverGame(
            group_id=group_id,
            owner_id=user_id,
            max_players=self.max_players,
            undercover_count=self.undercover_count,
            blank_count=self.blank_count,
        )
        game.add_player(user_id, name)
        self.games[group_id] = game
        return CommandOutcome(
            text="谁是卧底房间已创建。\n"
            f"人数：{len(game.players)}/{game.max_players}。",
            game=game,
        )

    def _join_game(self, group_id: str, user_id: str, name: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None or game.phase != GamePhase.WAITING:
            raise UndercoverError("当前没有等待加入的房间。")
        game.add_player(user_id, name)
        return CommandOutcome(
            text=f"{name} 已加入，当前 {len(game.players)}/{game.max_players} 人。",
            game=game,
        )

    def _leave_game(self, group_id: str, user_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None or game.phase != GamePhase.WAITING:
            raise UndercoverError("当前没有等待加入的房间。")
        game.remove_player(user_id)
        return CommandOutcome(text="已退出房间。", game=game)

    def _start_game(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise UndercoverError("当前没有谁是卧底房间。")
        if game.phase != GamePhase.WAITING:
            raise UndercoverError("游戏已经开始。")
        if user_id != game.owner_id and not self._is_admin(event):
            raise UndercoverError("只有房主或管理员可以开始游戏。")
        lines = game.start_game(random.Random())
        return CommandOutcome(text="\n".join(lines), game=game)

    def _show_status(self, group_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise UndercoverError("当前没有谁是卧底房间。")
        return CommandOutcome(text="\n".join(game.status_lines()), game=game)

    def _show_word(self, group_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None or game.phase == GamePhase.WAITING:
            raise UndercoverError("当前没有正在进行的游戏。")
        return CommandOutcome(
            text="看词。", game=game, buttons=self._word_buttons(game)
        )

    def _start_vote(self, group_id: str, user_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise UndercoverError("当前没有谁是卧底房间。")
        return self._action_outcome(group_id, game.start_vote(user_id))

    def _vote(self, group_id: str, user_id: str, text: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise UndercoverError("当前没有谁是卧底房间。")
        target = self._parse_int(text)
        return self._action_outcome(group_id, game.vote(user_id, target))

    def _tally(self, group_id: str, user_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise UndercoverError("当前没有谁是卧底房间。")
        return self._action_outcome(group_id, game.tally_votes(user_id))

    def _end_game(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise UndercoverError("当前没有谁是卧底房间。")
        if user_id != game.owner_id and not self._is_admin(event):
            raise UndercoverError("只有房主或管理员可以结束房间。")
        self.games.pop(group_id, None)
        return CommandOutcome(text="谁是卧底房间已结束。", game=None, buttons=[])

    def _action_outcome(self, group_id: str, lines: list[str]) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            return CommandOutcome(text="\n".join(lines), game=None, buttons=[])
        if game.phase == GamePhase.FINISHED:
            self.games.pop(group_id, None)
            return CommandOutcome(text="\n".join(lines), game=None, buttons=[])
        return CommandOutcome(text="\n".join(lines), game=game)

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def _menu_outcome(self) -> CommandOutcome:
        text = (
            "谁是卧底规则：\n"
            "1. 玩家分成平民、卧底，可能还有白板。\n"
            "2. 平民和卧底拿到相似但不同的词语；白板拿到“白板”。\n"
            "3. 每人点击自己的“看词”按钮查看词语，不要公开。\n"
            "4. 讨论后点击“开始投票”；所有存活玩家点击“投票”按钮并输入编号。\n"
            "5. 得票最多者出局，平票则无人出局。\n"
            "6. 全部卧底出局平民赢；卧底人数不少于平民时卧底赢。\n"
            "命令：卧底创建 / 卧底加入 / 卧底开始 / 卧底看 / "
            "我的词语 / 开始投票 / 投票 1 / 统计投票 / 卧底结束"
        )
        return CommandOutcome(text=text, buttons=self._menu_buttons())

    def _menu_buttons(self) -> list[ButtonSpec]:
        return [
            ButtonSpec("uc_menu_create", "创建房间", "卧底创建"),
            ButtonSpec("uc_menu_join", "加入", "卧底加入"),
            ButtonSpec("uc_menu_start", "开始", "卧底开始"),
            ButtonSpec("uc_menu_status", "状态", "卧底看"),
            ButtonSpec("uc_menu_word", "我的词语", "我的词语"),
            ButtonSpec("uc_menu_help", "卧底帮助", "卧底帮助"),
            ButtonSpec("uc_menu_end", "结束", "卧底结束"),
        ]

    def _game_buttons(self, game: UndercoverGame) -> list[ButtonSpec]:
        if game.phase == GamePhase.WAITING:
            return [
                ButtonSpec("uc_wait_join", "加入", "卧底加入"),
                ButtonSpec("uc_wait_start", "开始", "卧底开始", only_for=game.owner_id),
                ButtonSpec("uc_wait_status", "状态", "卧底看"),
                ButtonSpec("uc_wait_end", "结束", "卧底结束", only_for=game.owner_id),
            ]
        if game.phase == GamePhase.SPEAKING:
            buttons = self._word_buttons(game)
            buttons.extend(
                [
                    ButtonSpec("uc_act_start_vote", "开始投票", "开始投票"),
                    ButtonSpec("uc_act_status", "状态", "卧底看"),
                    ButtonSpec(
                        "uc_act_end", "结束", "卧底结束", only_for=game.owner_id
                    ),
                ]
            )
            return buttons
        if game.phase == GamePhase.VOTING:
            buttons = self._vote_buttons(game)
            buttons.extend(
                [
                    ButtonSpec("uc_act_tally", "统计投票", "统计投票"),
                    ButtonSpec("uc_act_status", "状态", "卧底看"),
                    ButtonSpec(
                        "uc_act_end", "结束", "卧底结束", only_for=game.owner_id
                    ),
                ]
            )
            return buttons
        return []

    def _word_buttons(self, game: UndercoverGame) -> list[ButtonSpec]:
        buttons: list[ButtonSpec] = []
        for index, player in enumerate(game.alive_players(), start=1):
            buttons.append(
                ButtonSpec(
                    f"uc_word_{index}",
                    f"{player.name} 看词",
                    f"卧底看词 {player.word}",
                    only_for=player.user_id,
                )
            )
        return buttons

    def _vote_buttons(self, game: UndercoverGame) -> list[ButtonSpec]:
        buttons: list[ButtonSpec] = []
        for index, player in enumerate(game.alive_players(), start=1):
            if player.user_id in game.votes:
                continue
            buttons.append(
                ButtonSpec(
                    f"uc_vote_{index}",
                    f"{player.name} 投票",
                    "投票 ",
                    only_for=player.user_id,
                )
            )
        return buttons

    # ------------------------------------------------------------------
    # Platform helpers
    # ------------------------------------------------------------------
    def _identity(self, event: AstrMessageEvent) -> tuple[str, str, str]:
        group_id = str(event.get_group_id() or "")
        user_id = str(event.get_sender_id() or "")
        name = str(event.get_sender_name() or "") or f"玩家_{user_id[-6:]}"
        return group_id, user_id, name

    async def _try_send_qqofficial(
        self, event: AstrMessageEvent, text: str, buttons: list[ButtonSpec] | None
    ) -> bool:
        if not is_qqofficial_event(event):
            return False
        context = extract_context(event)
        if context is None:
            return False
        return await send_group_reply(event, context, text, buttons or [])

    def _message_text(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            return str(getter() or "")
        return str(getattr(event, "message_str", "") or "")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:  # noqa: BLE001 - compatibility with test doubles
            return False

    def _parse_int(self, text: str) -> int:
        numbers = re.findall(r"\d+", str(text or ""))
        if not numbers:
            raise UndercoverError("格式错误，请发送“投票 编号”。")
        return int(numbers[-1])

    def _config_int(
        self,
        key: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw = self.config.get(key, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value
