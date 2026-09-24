from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from src.storage import UndercoverStore  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_builtin_import_is_idempotent_and_reversed_pairs_deduplicate(tmp_path) -> None:
    store = UndercoverStore(tmp_path / "word.sqlite3")
    run(store.init_db())

    run(store.import_builtin_pairs([("苹果", "梨"), ("梨", "苹果")]))
    stats = run(store.stats())

    assert stats["total"] == 1
    assert stats["builtin"] == 1
    assert stats["unused"] == 1


def test_custom_add_delete_and_builtin_protection(tmp_path) -> None:
    store = UndercoverStore(tmp_path / "word.sqlite3")
    run(store.init_db())
    run(store.import_builtin_pairs([("苹果", "梨")]))
    builtin_id = 1

    custom_id = run(store.add_custom("猫", "狗"))
    try:
        run(store.add_custom("狗", "猫"))
    except ValueError as exc:
        assert "已存在" in str(exc)
    else:  # pragma: no cover - guard against regression
        raise AssertionError("reversed duplicate custom pair should be rejected")

    assert run(store.delete_custom(int(custom_id))) is True
    assert run(store.delete_custom(builtin_id)) is False
    assert run(store.list_custom()) == []


def test_claim_and_release_pair(tmp_path) -> None:
    store = UndercoverStore(tmp_path / "word.sqlite3")
    run(store.init_db())
    run(store.import_builtin_pairs([("苹果", "梨")]))

    claimed = run(store.claim_unused_pair())
    assert claimed is not None
    pair_id, pair = claimed
    assert pair == ("梨", "苹果") or pair == ("苹果", "梨")
    assert run(store.stats())["unused"] == 0

    run(store.release_pair(pair_id))
    stats = run(store.stats())
    assert stats["unused"] == 1
    assert stats["used"] == 0
