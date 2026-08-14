import json

from app.core.game_logger import GameLogger


def _read_last_line(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()[-1]


def test_log_narration_writes_structured_record(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))
    logger.log_narration("g1", 1, "night", "天亮了", "昨晚是平安夜，没有人死亡。")
    record = json.loads(_read_last_line(tmp_path / "games" / "g1" / "game.log"))
    assert record["operation"] == "narration" and record["phase"] == "night"
    assert record["data"] == {"title": "天亮了", "text": "昨晚是平安夜，没有人死亡。"}
