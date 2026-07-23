"""Unit tests for utils.game_rules."""
import pytest
from utils.game_rules import (
    GAME_MIN_BET,
    GAME_MAX_BET,
    GAME_FEE_PERCENT,
    PVP_GAMES_HINT,
    parse_bet,
    validate_bet,
    pot_payout,
    bot_game_disabled_msg,
)


class TestConstants:
    def test_min_bet(self):
        assert GAME_MIN_BET == 250

    def test_max_bet(self):
        assert GAME_MAX_BET == 100_000

    def test_fee_percent(self):
        assert GAME_FEE_PERCENT == 5

    def test_pvp_hint_nonempty(self):
        assert "/bet" in PVP_GAMES_HINT
        assert "/roulette" in PVP_GAMES_HINT


class TestValidateBet:
    def test_below_min(self):
        ok, err = validate_bet(100)
        assert ok is False
        assert "250" in err

    def test_at_min(self):
        ok, err = validate_bet(250)
        assert ok is True
        assert err == ""

    def test_above_max(self):
        ok, err = validate_bet(100_001)
        assert ok is False
        assert "100000" in err

    def test_at_max(self):
        ok, err = validate_bet(100_000)
        assert ok is True
        assert err == ""

    def test_zero(self):
        ok, err = validate_bet(0)
        assert ok is False


class TestParseBet:
    def test_no_args_default(self):
        amt, err = parse_bet([], default=500)
        assert amt == 500
        assert err is None

    def test_no_args_no_default(self):
        amt, err = parse_bet([])
        assert amt is None
        assert "Usage" in err

    def test_valid_amount(self):
        amt, err = parse_bet(["500"])
        assert amt == 500
        assert err is None

    def test_invalid_string(self):
        amt, err = parse_bet(["abc"])
        assert amt is None
        assert "Invalid amount" in err


class TestPotPayout:
    def test_normal_five_percent(self):
        net = pot_payout(1000, premium=False)
        assert net == 950

    def test_premium_exempt(self):
        net = pot_payout(1000, premium=True)
        assert net == 1000

    def test_zero_pot(self):
        assert pot_payout(0, premium=False) == 0

    def test_rounding(self):
        net = pot_payout(250, premium=False)
        assert net == 237  # 250 * 0.95 = 237.5 -> int() -> 237

    def test_fractional_rounding(self):
        net = pot_payout(300, premium=False)
        assert net == 285  # 300 * 0.95 = 285.0


class TestBotGameDisabledMsg:
    def test_contains_game_name(self):
        msg = bot_game_disabled_msg("Dice")
        assert "Dice" in msg

    def test_contains_pvp_hint(self):
        msg = bot_game_disabled_msg("Slots")
        assert PVP_GAMES_HINT in msg

    def test_html_format(self):
        msg = bot_game_disabled_msg("Test")
        assert "<b>" in msg
        assert "</b>" in msg
