from __future__ import annotations

import random
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from src.engine import (  # noqa: E402
    ROLE_CIVILIAN,
    ROLE_UNDERCOVER,
    GamePhase,
    UndercoverError,
    UndercoverGame,
)


def make_game(count: int = 6) -> UndercoverGame:
    game = UndercoverGame("g", "u1", max_players=10, undercover_count=1, blank_count=0)
    for index in range(1, count + 1):
        game.add_player(f"u{index}", f"玩家{index}")
    game.start_game(random.Random(0))
    return game


def test_start_assigns_roles_words_and_speaking_phase() -> None:
    game = make_game(6)

    assert game.phase == GamePhase.SPEAKING
    assert len(game.alive_players()) == 6
    assert sum(1 for p in game.players if p.role == ROLE_UNDERCOVER) == 1
    assert sum(1 for p in game.players if p.role == ROLE_CIVILIAN) == 5
    assert {p.word for p in game.players} == {game.civilian_word, game.undercover_word}
    assert game.civilian_word != game.undercover_word


def test_vote_eliminates_player_and_civilian_win() -> None:
    game = make_game(4)
    target = game.players[1]
    target.role = ROLE_UNDERCOVER
    target.word = game.undercover_word
    for player in game.players:
        if player is not target:
            player.role = ROLE_CIVILIAN
            player.word = game.civilian_word

    game.start_vote("u1")
    for player in list(game.alive_players()):
        number = 2 if player.user_id != target.user_id else 1
        game.vote(player.user_id, number)

    assert target.alive is False
    assert game.phase == GamePhase.FINISHED
    assert game.winner == "平民阵营获胜。"


def test_tie_vote_eliminates_nobody() -> None:
    game = make_game(4)
    game.start_vote("u1")
    alive = game.alive_players()
    # 2 votes for 1, 2 votes for 2: a tie.
    game.vote(alive[1].user_id, 1)
    game.vote(alive[2].user_id, 1)
    game.vote(alive[0].user_id, 2)
    lines = game.vote(alive[3].user_id, 2)

    assert game.phase == GamePhase.SPEAKING
    assert all(player.alive for player in game.players)
    assert any("平票" in line for line in lines)


def test_undercover_side_wins_when_not_outnumbered() -> None:
    game = make_game(4)
    undercover = game.players[0]
    undercover.role = ROLE_UNDERCOVER
    for player in game.players[1:]:
        player.role = ROLE_CIVILIAN
    game.players[1].alive = False
    game.players[2].alive = False

    lines = game._check_winner()

    assert game.phase == GamePhase.FINISHED
    assert game.winner == "卧底阵营获胜。"
    assert any("身份公开" in line for line in lines)


def test_illegal_vote_is_rejected() -> None:
    game = make_game(4)
    try:
        game.vote("u1", 1)
    except UndercoverError as exc:
        assert "当前不在投票阶段" in str(exc)
    else:  # pragma: no cover - guard against regression
        raise AssertionError("vote out of phase should fail")
