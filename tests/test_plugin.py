from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

_SPEC = importlib.util.spec_from_file_location(
    "undercover_plugin_main", PLUGIN_DIR / "main.py"
)
assert _SPEC is not None and _SPEC.loader is not None
plugin_main = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = plugin_main
_SPEC.loader.exec_module(plugin_main)


class FakeAPI:
    def __init__(self) -> None:
        self.group_messages: list[dict] = []

    async def post_group_message(self, **payload):
        self.group_messages.append(payload)
        return {"id": str(len(self.group_messages))}


class FakeBot:
    def __init__(self) -> None:
        self.api = FakeAPI()


class FakeRawMessage:
    def __init__(self, member_openid: str) -> None:
        self.group_openid = "group-id"
        self.id = "message-id"
        self.msg_seq = 1
        self.author = SimpleNamespace(member_openid=member_openid)


class FakeEvent:
    def __init__(self, member_openid: str, name: str) -> None:
        self.bot = FakeBot()
        self.raw = FakeRawMessage(member_openid)
        self.message_obj = SimpleNamespace(
            raw_message=self.raw, message_id="message-id"
        )
        self.message_str = ""
        self._name = name
        self.stopped = False

    def get_platform_name(self) -> str:
        return "qq_official"

    def get_platform_id(self) -> str:
        return "qq_official"

    def get_group_id(self) -> str:
        return self.raw.group_openid

    def get_sender_id(self) -> str:
        return self.raw.author.member_openid

    def get_sender_name(self) -> str:
        return self._name

    def get_message_str(self) -> str:
        return self.message_str

    def is_admin(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


def run(coro):
    return asyncio.run(coro)


async def collect(asyncgen) -> list:
    return [item async for item in asyncgen]


def test_create_join_start_vote_and_eliminate_undercover() -> None:
    plugin = plugin_main.UndercoverPlugin(
        context=SimpleNamespace(),
        config={"max_players": 10, "undercover_count": 1, "blank_count": 0},
    )
    events = {
        "u1": FakeEvent("u1", "玩家1"),
        "u2": FakeEvent("u2", "玩家2"),
        "u3": FakeEvent("u3", "玩家3"),
        "u4": FakeEvent("u4", "玩家4"),
    }

    run(collect(plugin.create_command(events["u1"])))
    for user_id in ("u2", "u3", "u4"):
        run(collect(plugin.join_command(events[user_id])))
    run(collect(plugin.start_command(events["u1"])))

    game = plugin.games["group-id"]
    assert game.phase.value == "speaking"
    target = next(player for player in game.players if player.role == "undercover")

    run(collect(plugin.start_vote_command(events["u1"])))

    for user_id, event in events.items():
        game = plugin.games.get("group-id")
        if game is None:
            break
        if user_id == target.user_id:
            other = next(
                player for player in game.alive_players() if player.user_id != user_id
            )
            number = game.number_of(other.user_id)
        else:
            number = game.number_of(target.user_id)
        event.message_str = f"投票 {number}"
        run(collect(plugin.vote_command(event)))

    assert "group-id" not in plugin.games
    all_messages = [
        msg for event in events.values() for msg in event.bot.api.group_messages
    ]
    assert any("身份公开" in str(msg["markdown"]["content"]) for msg in all_messages)


def test_menu_contains_word_and_help_buttons() -> None:
    plugin = plugin_main.UndercoverPlugin(context=SimpleNamespace(), config={})
    labels = [button.label for button in plugin._menu_buttons()]
    assert "我的词语" in labels
    assert "卧底帮助" in labels
