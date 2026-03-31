"""Tests for the enrichment pipeline modules."""
import json
import types
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def _make_home(**kwargs):
    """Create a lightweight mock Home with the attributes prefilter needs."""
    defaults = {"address": "", "city": "", "url": "", "agency": "", "price": -1, "sqm": -1}
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Prefilter
# ---------------------------------------------------------------------------

class TestPrefilter:
    """Test prefilter.should_enqueue()"""

    def test_accepts_matching_listing(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=1200, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": ["Amsterdam", "Rotterdam"]}
        assert should_enqueue(home, profile) is True

    def test_rejects_over_budget(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=2000, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": ["Amsterdam"]}
        assert should_enqueue(home, profile) is False

    def test_rejects_wrong_city(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=1200, city="Utrecht")
        profile = {"max_rent": 1500, "target_cities": ["Amsterdam"]}
        assert should_enqueue(home, profile) is False

    def test_city_match_case_insensitive(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=1200, city="AMSTERDAM")
        profile = {"max_rent": 1500, "target_cities": ["amsterdam"]}
        assert should_enqueue(home, profile) is True

    def test_rejects_invalid_price(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=-1, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": ["Amsterdam"]}
        assert should_enqueue(home, profile) is False

    def test_rejects_zero_price(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=0, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": ["Amsterdam"]}
        assert should_enqueue(home, profile) is False

    def test_accepts_at_max_rent(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=1500, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": ["Amsterdam"]}
        assert should_enqueue(home, profile) is True

    def test_empty_target_cities(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=1200, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": []}
        assert should_enqueue(home, profile) is False

    def test_none_target_cities(self):
        from enrichment.prefilter import should_enqueue
        home = _make_home(price=1200, city="Amsterdam")
        profile = {"max_rent": 1500, "target_cities": None}
        assert should_enqueue(home, profile) is False


# ---------------------------------------------------------------------------
# Claude response parser
# ---------------------------------------------------------------------------

class TestClaudeResponseParser:
    """Test analyzer._parse_claude_response()"""

    def test_valid_json_array(self):
        from enrichment.analyzer import _parse_claude_response
        result = _parse_claude_response('[{"index": 0, "score": 8, "compatible": true}]')
        assert len(result) == 1
        assert result[0]["score"] == 8
        assert result[0]["compatible"] is True

    def test_markdown_fenced_json(self):
        from enrichment.analyzer import _parse_claude_response
        text = '```json\n[{"index": 0, "score": 7}]\n```'
        result = _parse_claude_response(text)
        assert len(result) == 1
        assert result[0]["score"] == 7

    def test_plain_fenced_json(self):
        from enrichment.analyzer import _parse_claude_response
        text = '```\n[{"index": 0, "score": 6}]\n```'
        result = _parse_claude_response(text)
        assert len(result) == 1
        assert result[0]["score"] == 6

    def test_truncated_json(self):
        from enrichment.analyzer import _parse_claude_response
        text = '[{"index": 0, "score": 8}, {"index": 1, "score": 7'
        result = _parse_claude_response(text)
        assert len(result) >= 1

    def test_empty_string(self):
        from enrichment.analyzer import _parse_claude_response
        assert _parse_claude_response("") == []

    def test_none_input(self):
        from enrichment.analyzer import _parse_claude_response
        assert _parse_claude_response(None) == []

    def test_whitespace_only(self):
        from enrichment.analyzer import _parse_claude_response
        assert _parse_claude_response("   \n  ") == []

    def test_random_text(self):
        from enrichment.analyzer import _parse_claude_response
        assert _parse_claude_response("This is not JSON at all") == []

    def test_single_object_not_array(self):
        from enrichment.analyzer import _parse_claude_response
        result = _parse_claude_response('{"index": 0, "score": 9}')
        assert len(result) == 1
        assert result[0]["score"] == 9

    def test_json_with_surrounding_text(self):
        from enrichment.analyzer import _parse_claude_response
        text = 'Here is the analysis:\n[{"index": 0, "score": 6}]\nDone.'
        result = _parse_claude_response(text)
        assert len(result) == 1
        assert result[0]["score"] == 6

    def test_multi_item_array(self):
        from enrichment.analyzer import _parse_claude_response
        items = [
            {"index": 0, "score": 8, "compatible": True},
            {"index": 1, "score": 3, "compatible": False},
            {"index": 2, "score": 5, "compatible": True},
        ]
        result = _parse_claude_response(json.dumps(items))
        assert len(result) == 3
        assert result[0]["score"] == 8
        assert result[1]["score"] == 3
        assert result[2]["score"] == 5


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------

class TestCostTracker:
    """Test costs.py functions"""

    def test_estimate_cost_haiku_input(self):
        from enrichment.costs import _estimate_cost
        cost = _estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0)
        assert abs(cost - 0.80) < 1e-9

    def test_estimate_cost_haiku_output(self):
        from enrichment.costs import _estimate_cost
        cost = _estimate_cost("claude-haiku-4-5-20251001", 0, 1_000_000)
        assert abs(cost - 4.00) < 1e-9

    def test_estimate_cost_sonnet_input(self):
        from enrichment.costs import _estimate_cost
        cost = _estimate_cost("claude-sonnet-4-20250514", 1_000_000, 0)
        assert abs(cost - 3.00) < 1e-9

    def test_estimate_cost_sonnet_output(self):
        from enrichment.costs import _estimate_cost
        cost = _estimate_cost("claude-sonnet-4-20250514", 0, 1_000_000)
        assert abs(cost - 15.00) < 1e-9

    def test_estimate_cost_unknown_model(self):
        from enrichment.costs import _estimate_cost
        cost = _estimate_cost("unknown-model", 1000, 500)
        assert cost == 0.0

    def test_estimate_cost_mixed(self):
        from enrichment.costs import _estimate_cost
        cost = _estimate_cost("claude-haiku-4-5-20251001", 10_000, 2_000)
        expected = (10_000 * 0.80 + 2_000 * 4.00) / 1_000_000
        assert abs(cost - expected) < 1e-9

    @patch("enrichment.costs.get_daily_spend", return_value=0.50)
    def test_budget_check_under_limit(self, _):
        from enrichment.costs import check_daily_budget
        assert check_daily_budget(limit=2.0) is True

    @patch("enrichment.costs.get_daily_spend", return_value=2.50)
    def test_budget_check_over_limit(self, _):
        from enrichment.costs import check_daily_budget
        assert check_daily_budget(limit=2.0) is False

    @patch("enrichment.costs.get_daily_spend", return_value=2.00)
    def test_budget_check_at_exact_limit(self, _):
        from enrichment.costs import check_daily_budget
        assert check_daily_budget(limit=2.0) is False


# ---------------------------------------------------------------------------
# Letter cache
# ---------------------------------------------------------------------------

class TestLetterCache:
    """Test letters.py caching behavior"""

    @patch("enrichment.letters.fetch_one")
    def test_returns_cached_letter(self, mock_fetch):
        mock_fetch.return_value = {"letter_nl": "Cached Dutch letter"}
        from enrichment.letters import generate_letter
        profile = {"id": 1, "full_name": "Test User"}
        verdict = {"id": "abc123", "listing": {}}
        result = generate_letter(profile, verdict, "nl")
        assert result == "Cached Dutch letter"

    @patch("enrichment.letters._write")
    @patch("enrichment.letters.log_usage")
    @patch("enrichment.letters.anthropic")
    @patch("enrichment.letters.build_system_prompt", return_value="system prompt")
    @patch("enrichment.letters.fetch_one", return_value={"letter_nl": None})
    def test_generates_and_caches(self, mock_fetch, mock_build, mock_anthropic, mock_log, mock_write):
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Generated letter")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 200
        mock_client.messages.create.return_value = mock_response

        from enrichment.letters import generate_letter
        profile = {"id": 1, "full_name": "Test User", "max_rent": 1500, "target_cities": ["Amsterdam"]}
        verdict = {
            "id": "result1",
            "listing": {"address": "Main St 10", "city": "Amsterdam", "rent_per_month": 1200},
        }
        result = generate_letter(profile, verdict, "nl")
        assert result == "Generated letter"
        mock_write.assert_called_once()

    @patch("enrichment.letters.time.sleep")
    @patch("enrichment.letters.anthropic")
    @patch("enrichment.letters.build_system_prompt", return_value="prompt")
    @patch("enrichment.letters.fetch_one", return_value={"letter_en": None})
    def test_handles_api_failure(self, mock_fetch, mock_build, mock_anthropic, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.APIError = Exception
        mock_client.messages.create.side_effect = Exception("API unavailable")

        from enrichment.letters import generate_letter
        profile = {"id": 1}
        verdict = {"id": "abc"}
        result = generate_letter(profile, verdict, "en")
        assert isinstance(result, str)
        assert "Failed" in result


# ---------------------------------------------------------------------------
# Enriched message formatting
# ---------------------------------------------------------------------------

class TestEnrichedMessage:
    """Test the enriched Telegram message formatting"""

    def test_markdownv2_escaping(self):
        from enrichment.analyzer import _esc
        assert _esc("test.value") == r"test\.value"
        assert _esc("(hello)") == r"\(hello\)"
        assert _esc("a-b") == r"a\-b"
        assert _esc("no_special") == r"no\_special"

    @pytest.mark.asyncio
    async def test_high_score_message_format(self):
        from enrichment.analyzer import _send_enriched_message
        import hermes_utils.meta as meta

        mock_send = AsyncMock()
        meta.BOT.send_message = mock_send

        verdict = {
            "score": 8,
            "listing": {
                "address": "Keizersgracht 42",
                "city": "Amsterdam",
                "rent_per_month": 1450,
                "size_m2": 65,
                "rooms": 2,
                "furnished_status": "gestoffeerd",
                "available_from": "1 May 2026",
                "energy_label": "A",
                "application_url": "https://example.com/apply",
            },
            "recommendation": "Strong match. Walking distance to office.",
            "trade_offs": ["+ Prime location", "- No parking"],
            "income_check": {"required_income": 4350, "user_income": 5000, "passes": True},
            "expat_flags": ["Requires gemeente inschrijving"],
        }

        await _send_enriched_message("12345", verdict, "https://example.com/listing", "resultid123")

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert "8/10" in kwargs["text"]
        assert kwargs["parse_mode"] == "MarkdownV2"
        assert kwargs["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_low_score_compact_format(self):
        from enrichment.analyzer import _send_low_score_summary
        import hermes_utils.meta as meta

        mock_send = AsyncMock()
        meta.BOT.send_message = mock_send

        low_scored = [
            {
                "item": {"address": "Bad St 1", "city": "Nowhere"},
                "verdict": {"score": 2, "rejection_reason": "Over budget"},
            },
            {
                "item": {"address": "Worse Rd 5", "city": "Far Away"},
                "verdict": {"score": 1, "rejection_reason": "Wrong city"},
            },
        ]

        await _send_low_score_summary("12345", low_scored)

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert "2 low-scored" in kwargs["text"] or "2 low\\-scored" in kwargs["text"]
        assert kwargs["parse_mode"] == "MarkdownV2"
        assert "reply_markup" not in kwargs

    @pytest.mark.asyncio
    async def test_low_score_empty_list_sends_nothing(self):
        from enrichment.analyzer import _send_low_score_summary
        import hermes_utils.meta as meta

        mock_send = AsyncMock()
        meta.BOT.send_message = mock_send

        await _send_low_score_summary("12345", [])

        mock_send.assert_not_called()
