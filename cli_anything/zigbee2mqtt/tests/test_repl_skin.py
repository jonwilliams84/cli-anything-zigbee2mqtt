"""Tests for the ReplSkin formatting and display logic.

These target the real behaviour: colour detection, prompt building,
table rendering, progress bars, and message formatting — not trivial
wiring or constants.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cli_anything.zigbee2mqtt.utils.repl_skin import (
    ReplSkin,
    _display_home_path,
    _strip_ansi,
    _visible_len,
)


# ── module-level helpers ────────────────────────────────────────────────


class TestStripAnsi:
    def test_removes_simple_color_code(self):
        assert _strip_ansi("\033[38;5;80mhello\033[0m") == "hello"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("no codes here") == "no codes here"

    def test_empty_string(self):
        assert _strip_ansi("") == ""

    def test_multiple_codes_removed(self):
        text = "\033[1m\033[38;5;196merror\033[0m\033[0m"
        assert _strip_ansi(text) == "error"


class TestVisibleLen:
    def test_counts_visible_chars_only(self):
        assert _visible_len("\033[38;5;80mhello\033[0m") == 5

    def test_plain_text(self):
        assert _visible_len("hello world") == 11

    def test_empty(self):
        assert _visible_len("") == 0


class TestDisplayHomePath:
    def test_path_under_home_shows_tilde(self, tmp_path, monkeypatch):
        """A path inside $HOME should be displayed as ~/relative."""
        fake_home = tmp_path / "myhome"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        sub = fake_home / "project" / "file.txt"
        sub.parent.mkdir(parents=True)
        sub.write_text("x")
        result = _display_home_path(str(sub))
        assert result == "~/project/file.txt"

    def test_path_outside_home_shows_absolute(self, tmp_path, monkeypatch):
        """A path outside $HOME should be displayed as an absolute path."""
        monkeypatch.setenv("HOME", str(tmp_path / "myhome"))
        other = tmp_path / "elsewhere"
        other.mkdir()
        result = _display_home_path(str(other))
        assert result == str(other.resolve())


# ── ReplSkin initialisation ─────────────────────────────────────────────


class TestReplSkinInit:
    def test_software_name_normalised(self, tmp_path, monkeypatch):
        """Hyphens in the software name should become underscores."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("my-software", version="2.0.0")
        assert skin.software == "my_software"
        assert skin.version == "2.0.0"

    def test_display_name_title_cased(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("zigbee2mqtt")
        assert skin.display_name == "Zigbee2Mqtt"

    def test_skill_slug_uses_alias(self, tmp_path, monkeypatch):
        """iterm2_ctl should map to iterm2 for the skill slug."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("iterm2_ctl")
        assert skin.skill_slug == "iterm2"
        assert skin.skill_id == "cli-anything-iterm2"

    def test_custom_history_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("test", history_file="/tmp/custom_hist")
        assert skin.history_file == "/tmp/custom_hist"

    def test_default_history_dir_created(self, tmp_path, monkeypatch):
        """When no history_file is given, ~/.cli-anything-<sw>/history is used."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("mytool")
        assert str(tmp_path / ".cli-anything-mytool" / "history") in skin.history_file
        assert (tmp_path / ".cli-anything-mytool").is_dir()


# ── colour detection ───────────────────────────────────────────────────


class TestColorDetection:
    def test_no_color_env_disables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("test")
        assert skin._color is False

    def test_cli_anything_no_color_env_disables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("CLI_ANYTHING_NO_COLOR", "1")
        skin = ReplSkin("test")
        assert skin._color is False

    def test_color_applied_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("test")
        # Force color on
        skin._color = True
        result = skin._c("\033[38;5;80m", "hello")
        assert "hello" in result
        assert "\033[" in result  # has ANSI codes
        assert result.endswith("\033[0m")

    def test_color_stripped_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        skin._color = False
        result = skin._c("\033[38;5;80m", "hello")
        assert result == "hello"


# ── prompt building ─────────────────────────────────────────────────────


class TestPrompt:
    def test_prompt_contains_software_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
        skin = ReplSkin("zigbee2mqtt")
        skin._color = False
        result = skin.prompt()
        assert "zigbee2mqtt" in result

    def test_prompt_with_project_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("zigbee2mqtt")
        skin._color = False
        result = skin.prompt(project_name="my_project")
        assert "my_project" in result

    def test_prompt_modified_shows_asterisk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("zigbee2mqtt")
        skin._color = False
        result = skin.prompt(project_name="proj", modified=True)
        assert "proj*" in result

    def test_prompt_not_modified_no_asterisk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("zigbee2mqtt")
        skin._color = False
        result = skin.prompt(project_name="proj", modified=False)
        assert "proj*" not in result

    def test_prompt_context_overrides_project_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("zigbee2mqtt")
        skin._color = False
        result = skin.prompt(project_name="proj", context="custom_ctx")
        assert "custom_ctx" in result
        assert "proj" not in result

    def test_prompt_tokens_structure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("zigbee2mqtt")
        tokens = skin.prompt_tokens(project_name="proj", modified=True)
        # tokens is a list of (style, text) tuples
        assert isinstance(tokens, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in tokens)
        # The software name should appear in the token text
        all_text = "".join(t[1] for t in tokens)
        assert "zigbee2mqtt" in all_text
        assert "proj*" in all_text


# ── message formatting ─────────────────────────────────────────────────


class TestMessages:
    @pytest.fixture
    def skin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        s = ReplSkin("test")
        s._color = False
        return s

    def test_success_prints_checkmark(self, skin, capsys):
        skin.success("done")
        out = capsys.readouterr().out
        assert "✓" in out
        assert "done" in out

    def test_error_prints_to_stderr(self, skin, capsys):
        skin.error("broken")
        err = capsys.readouterr().err
        assert "✗" in err
        assert "broken" in err

    def test_warning_prints_triangle(self, skin, capsys):
        skin.warning("careful")
        out = capsys.readouterr().out
        assert "⚠" in out
        assert "careful" in out

    def test_info_prints_dot(self, skin, capsys):
        skin.info("notice")
        out = capsys.readouterr().out
        assert "●" in out
        assert "notice" in out

    def test_hint_prints_message(self, skin, capsys):
        skin.hint("subtle")
        out = capsys.readouterr().out
        assert "subtle" in out

    def test_section_prints_title_and_separator(self, skin, capsys):
        skin.section("My Section")
        out = capsys.readouterr().out
        assert "My Section" in out
        assert "─" in out


# ── status display ─────────────────────────────────────────────────────


class TestStatusDisplay:
    @pytest.fixture
    def skin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        s = ReplSkin("test")
        s._color = False
        return s

    def test_status_prints_label_and_value(self, skin, capsys):
        skin.status("CPU", "42%")
        out = capsys.readouterr().out
        assert "CPU" in out
        assert "42%" in out

    def test_status_block_with_title(self, skin, capsys):
        skin.status_block({"a": "1", "b": "2"}, title="Info")
        out = capsys.readouterr().out
        assert "Info" in out
        assert "a" in out
        assert "1" in out
        assert "b" in out
        assert "2" in out

    def test_status_block_without_title(self, skin, capsys):
        skin.status_block({"x": "y"})
        out = capsys.readouterr().out
        assert "x" in out
        assert "y" in out

    def test_status_block_empty(self, skin, capsys):
        skin.status_block({})
        out = capsys.readouterr().out
        # Should not crash on empty dict
        assert out == ""


# ── progress ───────────────────────────────────────────────────────────


class TestProgress:
    @pytest.fixture
    def skin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        s = ReplSkin("test")
        s._color = False
        return s

    def test_progress_zero_percent(self, skin, capsys):
        skin.progress(0, 10)
        out = capsys.readouterr().out
        assert "  0%" in out

    def test_progress_fifty_percent(self, skin, capsys):
        skin.progress(5, 10)
        out = capsys.readouterr().out
        assert " 50%" in out

    def test_progress_hundred_percent(self, skin, capsys):
        skin.progress(10, 10)
        out = capsys.readouterr().out
        assert "100%" in out

    def test_progress_zero_total(self, skin, capsys):
        """When total is 0, percentage should be 0 (no ZeroDivisionError)."""
        skin.progress(0, 0)
        out = capsys.readouterr().out
        assert "  0%" in out

    def test_progress_with_label(self, skin, capsys):
        skin.progress(3, 10, label="processing")
        out = capsys.readouterr().out
        assert "processing" in out


# ── table rendering ─────────────────────────────────────────────────────


class TestTable:
    @pytest.fixture
    def skin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        s = ReplSkin("test")
        s._color = False
        return s

    def test_table_renders_headers_and_rows(self, skin, capsys):
        skin.table(["Name", "Value"], [["a", "1"], ["b", "2"]])
        out = capsys.readouterr().out
        assert "Name" in out
        assert "Value" in out
        assert "a" in out
        assert "1" in out
        assert "b" in out
        assert "2" in out

    def test_table_empty_headers_returns_immediately(self, skin, capsys):
        skin.table([], [["a", "b"]])
        out = capsys.readouterr().out
        assert out == ""

    def test_table_truncates_long_cells(self, skin, capsys):
        long_val = "x" * 100
        skin.table(["Col"], [[long_val]], max_col_width=10)
        out = capsys.readouterr().out
        # The cell should be truncated to max_col_width
        assert "x" * 10 in out
        assert long_val not in out

    def test_table_handles_more_columns_in_row(self, skin, capsys):
        """A row with more cells than headers should not crash."""
        skin.table(["A"], [["a", "extra"]])
        out = capsys.readouterr().out
        assert "A" in out
        assert "a" in out


# ── help display ───────────────────────────────────────────────────────


class TestHelp:
    def test_help_prints_commands_and_descriptions(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        skin._color = False
        skin.help({"list": "List items", "add": "Add an item"})
        out = capsys.readouterr().out
        assert "list" in out
        assert "List items" in out
        assert "add" in out
        assert "Add an item" in out

    def test_help_empty_commands(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        skin._color = False
        skin.help({})
        out = capsys.readouterr().out
        # Should print section header but no commands
        assert "Commands" in out


# ── goodbye ────────────────────────────────────────────────────────────


class TestGoodbye:
    def test_print_goodbye(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        skin._color = False
        skin.print_goodbye()
        out = capsys.readouterr().out
        assert "Goodbye" in out


# ── prompt_toolkit session ─────────────────────────────────────────────


class TestPromptSession:
    def test_create_prompt_session_returns_session(self, tmp_path, monkeypatch):
        """When prompt_toolkit is available, create_prompt_session should return a session."""
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        session = skin.create_prompt_session()
        # prompt_toolkit is a declared dependency, so this should not be None
        assert session is not None

    def test_get_input_with_fallback(self, tmp_path, monkeypatch):
        """When pt_session is None, get_input should use input() fallback."""
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        skin._color = False
        with patch("builtins.input", return_value="  hello  "):
            result = skin.get_input(None)
        assert result == "hello"

    def test_get_input_strips_whitespace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        skin._color = False
        with patch("builtins.input", return_value="\t  spaced  \n"):
            result = skin.get_input(None)
        assert result == "spaced"


# ── bottom toolbar ─────────────────────────────────────────────────────


class TestBottomToolbar:
    def test_toolbar_returns_formatted_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        toolbar = skin.bottom_toolbar({"key": "value"})
        result = toolbar()
        # Should return a FormattedText-like object (list of tuples)
        assert hasattr(result, "__iter__")
        # Flatten to check content
        text_parts = [item[1] for item in result]
        joined = "".join(text_parts)
        assert "key" in joined
        assert "value" in joined

    def test_toolbar_multiple_items_have_separator(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        toolbar = skin.bottom_toolbar({"a": "1", "b": "2"})
        result = toolbar()
        text_parts = [item[1] for item in result]
        joined = "".join(text_parts)
        assert "a" in joined
        assert "b" in joined
        assert "1" in joined
        assert "2" in joined
        # Should have a separator between items
        assert "│" in joined


# ── get_prompt_style ──────────────────────────────────────────────────


class TestGetPromptStyle:
    def test_returns_style_object(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        style = skin.get_prompt_style()
        # prompt_toolkit is a declared dependency
        assert style is not None
