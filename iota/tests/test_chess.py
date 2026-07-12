"""Unit tests for the self-contained chess engine (utils/chess_engine)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)

from utils.chess_engine import (
    initial_board, legal_moves, parse_move, parse_square, sq_name, apply_move,
    in_check, game_status, render_board, WHITE, BLACK,
)


class TestChessEngine(unittest.TestCase):
    def test_starting_legal_moves(self):
        b = initial_board()
        # White has exactly 20 legal opening moves
        self.assertEqual(len(legal_moves(b, WHITE)), 20)
        self.assertEqual(len(legal_moves(b, BLACK)), 20)

    def test_parse_move(self):
        self.assertEqual(parse_move("e2e4"), (parse_square("e2"),
                                             parse_square("e4"), None))
        self.assertEqual(parse_move("e7e8q"), (parse_square("e7"),
                                               parse_square("e8"), "Q"))
        self.assertIsNone(parse_move("hello"))

    def test_pawn_double_push(self):
        b = initial_board()
        moves = legal_moves(b, WHITE)
        self.assertIn((parse_square("e2"), parse_square("e4"), None), moves)

    def test_knight_from_corner(self):
        # knight on b1 reaches a3, c3
        b = initial_board()
        moves = [m for m in legal_moves(b, WHITE) if m[0] == parse_square("b1")]
        targets = {m[1] for m in moves}
        self.assertIn(parse_square("a3"), targets)
        self.assertIn(parse_square("c3"), targets)

    def test_king_safety_filters_moves(self):
        # White king on e3, black rook on e4 giving check along the file
        b = initial_board()
        b[parse_square("e1")] = None
        b[parse_square("e2")] = None
        b[parse_square("e3")] = "K"   # white king on e3
        b[parse_square("e7")] = None  # clear the pawn blocking the file
        b[parse_square("e4")] = "r"   # black rook checking down the e-file
        self.assertTrue(in_check(b, WHITE))

    def test_checkmate_detection(self):
        # Back-rank style mate: black king h8, white rook g8 + white king g7
        b = [None] * 64
        b[parse_square("h8")] = "k"   # black king
        b[parse_square("g8")] = "R"   # white rook giving check
        b[parse_square("g7")] = "K"   # white king defending the rook + covering escapes
        chk, mate, stale = game_status(b, BLACK)
        self.assertTrue(chk)
        self.assertTrue(mate)

    def test_apply_move_promotion(self):
        b = initial_board()
        b[parse_square("e7")] = "P"
        b[parse_square("e8")] = None
        nb = apply_move(b, parse_square("e7"), parse_square("e8"), "Q")
        self.assertEqual(nb[parse_square("e8")], "Q")

    def test_render_board_rows(self):
        b = initial_board()
        out = render_board(b)
        self.assertEqual(len(out.splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
