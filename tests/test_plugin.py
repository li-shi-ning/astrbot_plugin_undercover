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


def test_create_join_start_vote_and_eliminate_undercover(tmp_path) -> None:
    plugin = plugin_main.UndercoverPlugin(
        context=SimpleNamespace(),
        config={
            "max_players": 10,
            "undercover_count": 1,
            "blank_count": 0,
            "data_dir": str(tmp_path),
        },
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

    assert plugin.store is not None
    assert run(plugin.store.stats())["used"] == 1
    game = plugin.games["group-id"]
    assert game.phase.value == "speaking"
    target = next(player for player in game.players if player.role == "undercover")

    run(collect(plugin.start_vote_command(events["u1"])))
    vote_game = plugin.games["group-id"]
    vote_buttons = plugin._vote_buttons(vote_game)
    assert [button.label for button in vote_buttons] == [
        player.name for player in vote_game.alive_players()
    ]
    assert all(button.only_for is None for button in vote_buttons)

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
    assert "添加词库" in labels
    assert "自定义词库" in labels
    assert "删除自定义词库" in labels
    assert "词库状态" in labels
    assert "卧底帮助" in labels


def test_add_and_delete_custom_word_commands(tmp_path) -> None:
    plugin = plugin_main.UndercoverPlugin(
        context=SimpleNamespace(), config={"data_dir": str(tmp_path)}
    )
    event = FakeEvent("u1", "玩家1")
    event.message_str = "添加词库 测试词甲 测试词乙"
    run(collect(plugin.add_words_command(event)))

    assert plugin.store is not None
    rows = run(plugin.store.list_custom())
    assert len(rows) == 1
    pair_id = int(rows[0]["id"])

    event.message_str = f"删除自定义词库 {pair_id}"
    run(collect(plugin.delete_words_command(event)))
    assert run(plugin.store.list_custom()) == []


def test_start_message_has_hidden_word_button_per_player_and_speaker_button(
    tmp_path,
) -> None:
    plugin = plugin_main.UndercoverPlugin(
        context=SimpleNamespace(),
        config={
            "max_players": 10,
            "undercover_count": 1,
            "blank_count": 0,
            "data_dir": str(tmp_path),
        },
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

    payload = events["u1"].bot.api.group_messages[-1]
    keyboard = payload["keyboard"]
    buttons = [
        button for row in keyboard["content"]["rows"] for button in row["buttons"]
    ]
    labels = [button["render_data"]["label"] for button in buttons]

    assert sum("看词" in label for label in labels) == 4
    assert "发言结束" in labels
