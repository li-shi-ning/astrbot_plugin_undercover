from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

ROLE_CIVILIAN = "civilian"
ROLE_UNDERCOVER = "undercover"
ROLE_BLANK = "blank"

ROLE_LABELS = {
    ROLE_CIVILIAN: "平民",
    ROLE_UNDERCOVER: "卧底",
    ROLE_BLANK: "白板",
}

WORD_PAIRS: list[tuple[str, str]] = [
    ("可乐", "雪碧"),
    ("牛奶", "豆浆"),
    ("牙膏", "洗面奶"),
    ("眼镜", "隐形眼镜"),
    ("汉堡", "三明治"),
    ("毛笔", "钢笔"),
    ("医生", "护士"),
    ("空调", "风扇"),
    ("咖啡", "奶茶"),
    ("洗发水", "沐浴露"),
    ("手机", "平板"),
    ("键盘", "鼠标"),
    ("沙发", "床"),
    ("篮球", "足球"),
    ("西瓜", "哈密瓜"),
    ("包子", "饺子"),
    ("火锅", "麻辣烫"),
    ("老师", "教授"),
    ("警察", "保安"),
    ("微信", "QQ"),
    ("抖音", "快手"),
    ("微博", "朋友圈"),
    ("电影", "电视剧"),
    ("钢琴", "电子琴"),
    ("兔子", "仓鼠"),
    ("老虎", "狮子"),
    ("蚂蚁", "蜜蜂"),
    ("高铁", "地铁"),
    ("飞机", "火箭"),
    ("钢琴家", "歌唱家"),
]


def load_word_pairs(path: str | Path) -> list[tuple[str, str]]:
    """Load word pairs from a UTF-8 CSV with ``分类,词语A,词语B`` columns."""

    pairs: list[tuple[str, str]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 3:
                continue
            word_a = row[1].strip()
            word_b = row[2].strip()
            if not word_a or not word_b or word_a in {"词语A", "词A"}:
                continue
            pairs.append((word_a, word_b))
    return pairs


DEFAULT_MAX_PLAYERS = 10
DEFAULT_MIN_PLAYERS = 4


class UndercoverError(ValueError):
    """Raised when an Undercover action is not legal."""


class GamePhase(str, Enum):
    WAITING = "waiting"
    SPEAKING = "speaking"
    VOTING = "voting"
    FINISHED = "finished"


@dataclass
class PlayerState:
    user_id: str
    name: str
    alive: bool = True
    role: str = ROLE_CIVILIAN
    word: str = ""

    @property
    def role_label(self) -> str:
        """Return the Chinese label for this player's role."""

        return ROLE_LABELS.get(self.role, self.role)


@dataclass
class UndercoverGame:
    group_id: str
    owner_id: str
    max_players: int = DEFAULT_MAX_PLAYERS
    undercover_count: int = 1
    blank_count: int = 0
    word_pairs: list[tuple[str, str]] = field(default_factory=list)
    players: list[PlayerState] = field(default_factory=list)
    phase: GamePhase = GamePhase.WAITING
    round_no: int = 0
    current_speaker_index: int = 0
    votes: dict[str, str] = field(default_factory=dict)
    civilian_word: str = ""
    undercover_word: str = ""
    winner: str | None = None

    def get_player(self, user_id: str) -> PlayerState | None:
        """Return a player by user id, or ``None`` when absent."""

        return next(
            (player for player in self.players if player.user_id == user_id), None
        )

    def alive_players(self) -> list[PlayerState]:
        """Return players who have not been eliminated."""

        return [player for player in self.players if player.alive]

    def current_speaker(self) -> PlayerState | None:
        """Return the player whose turn it is to speak."""

        alive = self.alive_players()
        if not alive:
            return None
        return alive[self.current_speaker_index % len(alive)]

    def alive_number(self, number: int) -> PlayerState:
        """Resolve a 1-based alive player number."""

        alive = self.alive_players()
        number = int(number)
        if not 1 <= number <= len(alive):
            raise UndercoverError(f"玩家编号必须是 1-{len(alive)}。")
        return alive[number - 1]

    def number_of(self, user_id: str) -> int:
        """Return the current alive-list number for a player."""

        for index, player in enumerate(self.alive_players(), start=1):
            if player.user_id == user_id:
                return index
        raise UndercoverError("你当前不在存活玩家中。")

    def add_player(self, user_id: str, name: str) -> None:
        """Add a player to the waiting room."""

        if self.phase != GamePhase.WAITING:
            raise UndercoverError("游戏已经开始，不能加入。")
        if self.get_player(user_id) is not None:
            raise UndercoverError("你已经加入本局。")
        if len(self.players) >= self.max_players:
            raise UndercoverError("房间人数已满。")
        self.players.append(PlayerState(user_id=user_id, name=name))

    def remove_player(self, user_id: str) -> None:
        """Remove a player from the waiting room."""

        if self.phase != GamePhase.WAITING:
            raise UndercoverError("游戏已经开始，不能退出。")
        player = self.get_player(user_id)
        if player is None:
            raise UndercoverError("你还没有加入本局。")
        if player.user_id == self.owner_id:
            raise UndercoverError("房主不能退出，请直接结束房间。")
        self.players.remove(player)

    def start_game(
        self,
        rng: random.Random | None = None,
        word_pair: tuple[str, str] | None = None,
    ) -> list[str]:
        """Assign words and roles, then enter the speaking phase.

        Args:
            rng: Optional random source.
            word_pair: Optional orientation-specific pair ``(civilian, undercover)``
                drawn from the SQL word store.
        """

        if self.phase != GamePhase.WAITING:
            raise UndercoverError("游戏已经开始。")
        if len(self.players) < DEFAULT_MIN_PLAYERS:
            raise UndercoverError(f"至少需要 {DEFAULT_MIN_PLAYERS} 人才能开始。")
        rng = rng or random.Random()
        if word_pair is None:
            civilian_word, undercover_word = rng.choice(self.word_pairs or WORD_PAIRS)
        else:
            civilian_word, undercover_word = word_pair
        self.civilian_word = civilian_word
        self.undercover_word = undercover_word
        self.winner = None
        self.votes = {}
        self.round_no += 1
        for player in self.players:
            player.alive = True
            player.role = ROLE_CIVILIAN
            player.word = civilian_word
        rng.shuffle(self.players)
        role_order: list[str] = []
        role_order.extend(
            [ROLE_UNDERCOVER] * min(self.undercover_count, len(self.players))
        )
        remaining = len(self.players) - len(role_order)
        blank = min(self.blank_count, max(0, remaining - 1))
        role_order.extend([ROLE_BLANK] * blank)
        while len(role_order) < len(self.players):
            role_order.append(ROLE_CIVILIAN)
        rng.shuffle(role_order)
        for player, role in zip(self.players, role_order):
            player.role = role
            if role == ROLE_UNDERCOVER:
                player.word = undercover_word
            elif role == ROLE_BLANK:
                player.word = "白板"
            else:
                player.word = civilian_word
        self.phase = GamePhase.SPEAKING
        self.current_speaker_index = 0
        first = self.current_speaker()
        lines = [
            f"游戏开始，共 {len(self.players)} 人。",
            "请点击自己的“看词”按钮查看词语。",
        ]
        if first is not None:
            lines.append(f"轮到 {first.name} 发言。")
        return lines

    def view_word(self, user_id: str) -> str:
        """Return the word shown to one player."""

        player = self.get_player(user_id)
        if player is None or not player.alive:
            raise UndercoverError("你不在当前游戏中。")
        if not player.word:
            raise UndercoverError("当前没有可查看的词语。")
        return player.word

    def start_vote(self, user_id: str) -> list[str]:
        """Open voting for all alive players."""

        if self.phase != GamePhase.SPEAKING:
            raise UndercoverError("当前不能开始投票。")
        player = self.get_player(user_id)
        if player is None or not player.alive:
            raise UndercoverError("只有存活玩家可以发起投票。")
        self.phase = GamePhase.VOTING
        self.votes = {}
        return [f"{player.name} 发起了投票。", "请所有存活玩家发送“投票 编号”。"]

    def finish_speaking(self, user_id: str) -> list[str]:
        """End the current player's speech and pass to the next alive player."""

        if self.phase != GamePhase.SPEAKING:
            raise UndercoverError("当前不在发言阶段。")
        speaker = self.current_speaker()
        if speaker is None or speaker.user_id != user_id:
            raise UndercoverError("现在还没有轮到你发言。")
        alive = self.alive_players()
        self.current_speaker_index = (self.current_speaker_index + 1) % len(alive)
        next_speaker = self.current_speaker()
        lines = [f"{speaker.name} 发言结束。"]
        if next_speaker is not None:
            lines.append(f"轮到 {next_speaker.name} 发言。")
        return lines

    def vote(self, voter_id: str, target_number: int) -> list[str]:
        """Record one vote, automatically tallying when everyone has voted."""

        if self.phase != GamePhase.VOTING:
            raise UndercoverError("当前不在投票阶段。")
        voter = self.get_player(voter_id)
        if voter is None or not voter.alive:
            raise UndercoverError("只有存活玩家可以投票。")
        if voter_id in self.votes:
            raise UndercoverError("你已经投过票了。")
        target = self.alive_number(target_number)
        if target.user_id == voter_id:
            raise UndercoverError("不能投票给自己。")
        self.votes[voter_id] = target.user_id
        lines = [f"{voter.name} 已投票。"]
        if len(self.votes) >= len(self.alive_players()):
            lines.extend(self._tally_votes())
        return lines

    def tally_votes(self, user_id: str) -> list[str]:
        """Tally the current votes manually."""

        if self.phase != GamePhase.VOTING:
            raise UndercoverError("当前不在投票阶段。")
        player = self.get_player(user_id)
        if player is None or not player.alive:
            raise UndercoverError("只有存活玩家可以统计投票。")
        return self._tally_votes()

    def status_lines(self) -> list[str]:
        """Build public status text."""

        labels = {
            GamePhase.WAITING: "等待加入",
            GamePhase.SPEAKING: "发言/讨论",
            GamePhase.VOTING: "投票中",
            GamePhase.FINISHED: "已结束",
        }
        lines = [f"阶段：{labels[self.phase]}"]
        if self.phase == GamePhase.SPEAKING:
            speaker = self.current_speaker()
            if speaker is not None:
                lines.append(f"当前发言人：{speaker.name}")
        if self.phase == GamePhase.VOTING:
            lines.append(f"投票已投：{len(self.votes)}/{len(self.alive_players())}")
        lines.append("存活玩家：")
        for index, player in enumerate(self.alive_players(), start=1):
            lines.append(f"{index}. {player.name}")
        if self.phase == GamePhase.FINISHED and self.winner:
            lines.append(self.winner)
        return lines

    def _tally_votes(self) -> list[str]:
        """Eliminate the highest-voted player and check victory."""

        if not self.votes:
            raise UndercoverError("还没有人投票。")
        counts: dict[str, int] = {}
        for target_id in self.votes.values():
            counts[target_id] = counts.get(target_id, 0) + 1
        maximum = max(counts.values())
        top = [user_id for user_id, count in counts.items() if count == maximum]
        if len(top) > 1:
            self.votes = {}
            self.phase = GamePhase.SPEAKING
            self.current_speaker_index = 0
            return ["平票，本轮无人出局。", "请继续发言后重新投票。"]
        eliminated = next(player for player in self.players if player.user_id == top[0])
        eliminated.alive = False
        lines = [
            f"{eliminated.name} 被投票出局。",
            f"身份：{eliminated.role_label}，词语：{eliminated.word}。",
        ]
        self.votes = {}
        self.current_speaker_index = 0
        winner_lines = self._check_winner()
        if winner_lines:
            lines.extend(winner_lines)
        else:
            self.phase = GamePhase.SPEAKING
            lines.append("请继续发言，之后可再次发起投票。")
        return lines

    def _check_winner(self) -> list[str]:
        """Set the winner and return reveal lines when the game is over."""

        alive_undercover = sum(
            1
            for player in self.alive_players()
            if player.role in {ROLE_UNDERCOVER, ROLE_BLANK}
        )
        alive_civilian = sum(
            1 for player in self.alive_players() if player.role == ROLE_CIVILIAN
        )
        if alive_undercover == 0:
            self.winner = "平民阵营获胜。"
        elif alive_undercover >= alive_civilian:
            self.winner = "卧底阵营获胜。"
        else:
            return []
        self.phase = GamePhase.FINISHED
        lines = [self.winner, "身份公开："]
        for player in self.players:
            lines.append(f"- {player.name}：{player.role_label}，词语：{player.word}")
        return lines

    def to_summary(self) -> dict[str, Any]:
        """Serialize the game state for diagnostics or tests."""

        return {
            "group_id": self.group_id,
            "owner_id": self.owner_id,
            "phase": self.phase.value,
            "round_no": self.round_no,
            "civilian_word": self.civilian_word,
            "undercover_word": self.undercover_word,
            "votes": dict(self.votes),
            "players": [
                {
                    "user_id": player.user_id,
                    "name": player.name,
                    "alive": player.alive,
                    "role": player.role,
                    "word": player.word,
                }
                for player in self.players
            ],
        }
