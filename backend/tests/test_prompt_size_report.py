from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

SCRIPT = Path(__file__).parents[1] / "scripts" / "prompt_size_report.py"
SPEC = importlib.util.spec_from_file_location("prompt_size_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)


CONTRACT_USER = (
    "ROLE_CONTRACT={\"instructions\":\"brief\"}\n"
    "PROJECTED_CONTEXT={\"facts\":{\"public_role_rules\":[{\"id\":\"r\"}],\"speeches\":[{\"t\":1}]}}\n"
    "OUTPUT_ACTION_COMMAND_SCHEMA={\"type\":\"object\"}\n"
    "TARGET_SELECTION_RULE=choose\n"
    "<public_role_rules>{\"rules\":[]}</public_role_rules>\n"
    "<untrusted_action_history><record>line</record></untrusted_action_history>"
)
DAY_USER_DUPLICATED = (
    "<authoritative_state>\n## 你的私有事实\n"
    "- public_role_rules：[{\"id\":\"r\"}]\n- speeches：[{\"t\":1}]\n"
    "</authoritative_state>\n"
    "<public_role_rules>{\"rules\":[]}</public_role_rules>\n"
    "## 历史与对话\nrecord\n"
)
DAY_USER_DEDUPED = (
    "<authoritative_state>\n## 你的私有事实\n- 可用资源：gun=1\n"
    "</authoritative_state>\n"
    "<public_role_rules>{\"rules\":[]}</public_role_rules>\n"
    "## 历史与对话\nrecord\n"
)


def _record(call_kind: str, user: str, system: str = "SYS") -> dict:
    return {
        "round": 1, "phase": "day", "operation": "llm_prompt", "seat": 1,
        "data": {
            "call_kind": call_kind,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    }


def _write(tmp_path: Path, records: list[dict], *, game: str = "g1", extra: str = "") -> Path:
    directory = tmp_path / "games" / game
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / report.PROMPT_CAPTURE_FILENAME
    body = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
    path.write_text(f"{body}\n{extra}", encoding="utf-8")
    return path


def test_classify_splits_paths_sections_and_duplicate_flags() -> None:
    contract = report.classify(_record("pipeline", CONTRACT_USER))
    assert contract["path"] == "contract"
    assert contract["prompt_chars"] == len("SYS") + len(CONTRACT_USER)
    assert contract["dup_role_rules_projected"] is True
    assert contract["dup_speeches_projected"] is True
    assert contract["dup_role_rules_dump"] is False
    assert contract["sections"]["ROLE_CONTRACT"] > 0
    assert contract["sections"]["history"] > 0

    day = report.classify(_record("speech", DAY_USER_DUPLICATED))
    assert day["path"] == "day"
    assert day["dup_role_rules_dump"] is True and day["dup_speeches_dump"] is True
    assert day["sections"]["private_facts"] > 0

    deduped = report.classify(_record("speech", DAY_USER_DEDUPED))
    assert deduped["dup_role_rules_dump"] is False and deduped["dup_speeches_dump"] is False
    assert deduped["sections"]["private_facts"] > 0

    other = report.classify({"data": {"call_kind": None, "messages": [{"role": "user", "content": "x"}]}})
    assert other["path"] == "other" and other["call_kind"] == "unknown"
    assert other["sections"] == {}


def test_classify_tolerates_missing_message_fields() -> None:
    item = report.classify({"data": {"messages": [{"role": "user"}, {"role": "system", "content": None}]}})
    assert item["prompt_chars"] == 0


def test_load_records_skips_blank_and_malformed_lines_and_honours_limit(tmp_path: Path) -> None:
    _write(tmp_path, [_record("speech", DAY_USER_DEDUPED)], game="g1", extra="\nnot json\n")
    _write(tmp_path, [_record("pipeline", CONTRACT_USER)], game="g2")

    assert len(report.load_records(str(tmp_path))) == 2
    assert len(report.load_records(str(tmp_path), limit=1)) == 1


def test_summarize_aggregates_and_counts_duplicates(tmp_path: Path) -> None:
    _write(tmp_path, [
        _record("speech", DAY_USER_DUPLICATED),
        _record("speech", DAY_USER_DEDUPED),
    ])
    records = report.load_records(str(tmp_path))

    summary = report.summarize(records)

    assert summary["files_scanned"] == 2
    speech = summary["kinds"]["speech"]
    assert speech["records"] == 2 and speech["paths"] == ["day"]
    assert speech["prompt_chars"]["p50"] is not None
    assert summary["duplicates"]["role_rules_dumped_again"] == 1
    assert summary["duplicates"]["speeches_dumped_again"] == 1
    assert summary["duplicates"]["role_rules_in_projected"] == 0


def test_render_report_lists_sections_and_duplicate_counters(tmp_path: Path) -> None:
    _write(tmp_path, [_record("pipeline", CONTRACT_USER)])
    rendered = report.render_report(report.summarize(report.load_records(str(tmp_path))))
    assert "捕获记录数: 1" in rendered
    assert "[pipeline]" in rendered
    assert "PROJECTED_CONTEXT" in rendered
    assert "重复事实检测（应为 0）" in rendered
    assert "role_rules_in_projected    1" in rendered


def test_main_reports_missing_capture_and_emits_json(tmp_path: Path, capsys) -> None:
    assert report.main(["--data-dir", str(tmp_path)]) == 2
    assert "llm_prompts.jsonl" in capsys.readouterr().err

    _write(tmp_path, [_record("speech", DAY_USER_DEDUPED)])
    assert report.main(["--data-dir", str(tmp_path)]) == 0
    assert "捕获记录数: 1" in capsys.readouterr().out

    assert report.main(["--data-dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files_scanned"] == 1


def test_sections_are_found_in_any_order_and_repeated_blocks_are_summed() -> None:
    reordered = (
        "<public_role_rules>{\"rules\":[]}</public_role_rules>\n"
        "ROLE_CONTRACT={\"instructions\":\"brief\"}\n"
        "<untrusted_action_history><record>line</record></untrusted_action_history>\n"
        "PROJECTED_CONTEXT={\"facts\":{}}"
    )
    contract = report.classify(_record("pipeline", reordered))
    assert contract["sections"]["PROJECTED_CONTEXT"] == len("PROJECTED_CONTEXT={\"facts\":{}}")
    assert contract["sections"]["ROLE_CONTRACT"] == len("ROLE_CONTRACT={\"instructions\":\"brief\"}\n")

    day = report.classify(_record("speech", (
        "<untrusted_public_history>\nx\n</untrusted_public_history>\n"
        "<authoritative_state>a</authoritative_state>\n"
        "<authoritative_state>bb</authoritative_state>"
    )))
    assert day["sections"]["authoritative_state"] == 2 * len("<authoritative_state></authoritative_state>") + 3
    assert day["sections"]["public_history"] > 0
    assert day["sections"]["self_history"] == 0


def test_prefix_reuse_measures_the_longest_prefix_shared_with_earlier_prompts() -> None:
    def record(kind: str, user: str, model: str | None = "m") -> dict:
        item = _record(kind, user)
        item["data"]["model_id"] = model
        return item

    result = report.prefix_reuse([
        record("speech", "RULES|seat1"),
        record("speech", "RULES|seat2"),
        record("night", "RULES|seat1|more"),
        record("speech", "RULES|seat2", model="other"),
        {"data": {"messages": None}},
    ])

    system = len("<system>SYS<user>")
    rules = system + len("RULES|seat")
    assert result["kinds"]["speech"]["records"] == 3
    assert result["kinds"]["speech"]["reusable_chars"] == rules
    assert result["kinds"]["night"]["reusable_chars"] == system + len("RULES|seat1")
    assert result["kinds"]["unknown"] == {
        "records": 1, "prompt_chars": 0, "reusable_chars": 0, "reuse_rate": None,
    }
    assert result["records"] == 5
    assert 0 < result["reuse_rate"] < 1


def test_prefix_reuse_of_nothing_is_unknown() -> None:
    assert report.prefix_reuse([]) == {
        "records": 0, "prompt_chars": 0, "reusable_chars": 0,
        "reuse_rate": None, "kinds": {},
    }


def test_main_prefix_flag_adds_the_reuse_report(tmp_path: Path, capsys) -> None:
    _write(tmp_path, [_record("speech", DAY_USER_DEDUPED), _record("speech", DAY_USER_DEDUPED)])

    assert report.main(["--data-dir", str(tmp_path), "--prefix"]) == 0
    out = capsys.readouterr().out
    assert "前缀可复用上限" in out
    assert "50.0%" in out

    assert report.main(["--data-dir", str(tmp_path), "--prefix", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prefix_reuse"]["reuse_rate"] == 0.5


def test_render_report_shows_a_dash_for_an_unknown_reuse_rate() -> None:
    summary = {
        "files_scanned": 0, "kinds": {}, "duplicates": {},
        "prefix_reuse": {"reuse_rate": None, "kinds": {
            "speech": {"records": 1, "reuse_rate": None},
        }},
    }
    rendered = report.render_report(summary)
    assert "前缀可复用上限（同模型历史请求的最长公共前缀）: —" in rendered
