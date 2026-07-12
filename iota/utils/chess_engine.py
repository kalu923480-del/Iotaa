"""
Iota Bot — Chess Engine (self-contained, no external deps)

Pure, testable chess logic used by handlers/chess.py. Supports standard
rules: piece moves, castling, en passant, promotion, check / checkmate /
stalemate detection. Board is a flat list of 64 cells (None = empty),
index = rank*8 + file, rank 0 = rank '1' (White home row), file 0 = 'a'.
White pieces are uppercase, Black lowercase.
"""
import copy

WHITE, BLACK = "w", "b"
PIECES = set("PNBRQKpnbrqk")


def initial_board():
    b = [None] * 64
    back = "RNBQKBNR"
    for f in range(8):
        b[f] = back[f]                     # rank 1 (white)
        b[8 + f] = "P"
        b[48 + f] = "p"
        b[56 + f] = back[f].lower()        # rank 8 (black)
    return b


def sq_name(idx):
    return "abcdefgh"[idx % 8] + str(idx // 8 + 1)


def parse_square(name):
    name = name.strip().lower()
    if len(name) != 2 or name[0] not in "abcdefgh" or name[1] not in "12345678":
        return None
    return (int(name[1]) - 1) * 8 + "abcdefgh".index(name[0])


def color_of(piece):
    return WHITE if piece.isupper() else BLACK


def on_board(f, r):
    return 0 <= f < 8 and 0 <= r < 8


def idx_of(f, r):
    return r * 8 + f


def _step(board, f, r, df, dr, color, moves, sliding=False, cap_only=False):
    nf, nr = f + df, r + dr
    while on_board(nf, nr):
        t = board[idx_of(nf, nr)]
        if t is None:
            if not cap_only:
                moves.append(idx_of(nf, nr))
        else:
            if color_of(t) != color:
                moves.append(idx_of(nf, nr))
            break
        if not sliding:
            break
        nf += df
        nr += dr


def knight_moves(f, r, color, board):
    out = []
    for df, dr in ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1),
                   (-2, 1), (-1, 2)):
        nf, nr = f + df, r + dr
        if on_board(nf, nr):
            t = board[idx_of(nf, nr)]
            if t is None or color_of(t) != color:
                out.append(idx_of(nf, nr))
    return out


def king_moves(f, r, color, board):
    out = []
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = f + df, r + dr
            if on_board(nf, nr):
                t = board[idx_of(nf, nr)]
                if t is None or color_of(t) != color:
                    out.append(idx_of(nf, nr))
    return out


def pawn_moves(idx, color, board, ep_target=None):
    f, r = idx % 8, idx // 8
    out = []
    dr = 1 if color == WHITE else -1
    start_rank = 1 if color == WHITE else 6
    promo_rank = 7 if color == WHITE else 0
    # forward
    nr = r + dr
    if on_board(f, nr) and board[idx_of(f, nr)] is None:
        if nr == promo_rank:
            out.extend((idx_of(f, nr), "Q"))
            out.extend((idx_of(f, nr), "R"))
            out.extend((idx_of(f, nr), "B"))
            out.extend((idx_of(f, nr), "N"))
        else:
            out.append(idx_of(f, nr))
        # double
        if r == start_rank:
            nr2 = r + 2 * dr
            if board[idx_of(f, nr2)] is None:
                out.append(idx_of(f, nr2))
    # captures
    for cf in (f - 1, f + 1):
        if on_board(cf, r + dr):
            t = board[idx_of(cf, r + dr)]
            if t is not None and color_of(t) != color:
                if (r + dr) == promo_rank:
                    out.extend((idx_of(cf, r + dr), "Q"))
                    out.extend((idx_of(cf, r + dr), "R"))
                    out.extend((idx_of(cf, r + dr), "B"))
                    out.extend((idx_of(cf, r + dr), "N"))
                else:
                    out.append(idx_of(cf, r + dr))
            elif ep_target is not None and idx_of(cf, r + dr) == ep_target:
                out.append(idx_of(cf, r + dr))
    return out


def pseudo_moves(board, idx, ep_target=None):
    """Return a flat list mixing int targets and (target, promo) tuples."""
    piece = board[idx]
    if piece is None:
        return []
    color = color_of(piece)
    f, r = idx % 8, idx // 8
    out = []
    p = piece.upper()
    if p == "P":
        out = pawn_moves(idx, color, board, ep_target)
    elif p == "N":
        out = knight_moves(f, r, color, board)
    elif p == "K":
        out = king_moves(f, r, color, board)
        # two kings can never be adjacent — exclude the enemy king square
        out = [t for t in out if not (board[t] and board[t].upper() == "K")]
    elif p == "B":
        for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            _step(board, f, r, df, dr, color, out, sliding=True)
    elif p == "R":
        for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            _step(board, f, r, df, dr, color, out, sliding=True)
    elif p == "Q":
        for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1),
                       (1, 0), (-1, 0), (0, 1), (0, -1)):
            _step(board, f, r, df, dr, color, out, sliding=True)
    return out


def find_king(board, color):
    k = "K" if color == WHITE else "k"
    for i, p in enumerate(board):
        if p == k:
            return i
    return -1


def is_attacked(board, idx, by_color):
    """Is square `idx` attacked by any piece of `by_color`?"""
    f, r = idx % 8, idx // 8
    # pawn attacks: a `by_color` pawn attacks diagonally upward toward its side
    if by_color == WHITE:
        for cf in (f - 1, f + 1):
            if on_board(cf, r - 1) and board[idx_of(cf, r - 1)] == "P":
                return True
    else:
        for cf in (f - 1, f + 1):
            if on_board(cf, r + 1) and board[idx_of(cf, r + 1)] == "p":
                return True
    # knights
    for t in knight_moves(f, r, by_color, board):
        if board[t] and board[t].upper() == "N" and color_of(board[t]) == by_color:
            return True
    # king: an enemy king on any square adjacent to `idx` attacks it
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = f + df, r + dr
            if on_board(nf, nr):
                t = board[idx_of(nf, nr)]
                if t is not None and t.upper() == "K" and color_of(t) == by_color:
                    return True
    # sliding: bishop/queen diagonals
    for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        nf, nr = f + df, r + dr
        while on_board(nf, nr):
            t = board[idx_of(nf, nr)]
            if t is not None:
                if color_of(t) == by_color and t.upper() in ("B", "Q"):
                    return True
                break
            nf += df
            nr += dr
    # rook/queen straight
    for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nf, nr = f + df, r + dr
        while on_board(nf, nr):
            t = board[idx_of(nf, nr)]
            if t is not None:
                if color_of(t) == by_color and t.upper() in ("R", "Q"):
                    return True
                break
            nf += df
            nr += dr
    return False


def in_check(board, color):
    opp = BLACK if color == WHITE else WHITE
    return is_attacked(board, find_king(board, color), opp)


def apply_move(board, frm, to, promo=None, ep_target=None, castling=None):
    """Return a NEW board after applying the move (handles ep/castle/promo).
    Does NOT validate legality — callers filter via legal_moves()."""
    b = board[:]
    piece = b[frm]
    color = color_of(piece)
    b[to] = piece
    b[frm] = None
    # en passant capture
    if piece.upper() == "P" and ep_target is not None and to == ep_target \
            and b[to] is None:
        cap_rank = (to // 8) - 1 if color == WHITE else (to // 8) + 1
        b[idx_of(to % 8, cap_rank)] = None
        b[to] = piece
    # promotion
    if piece.upper() == "P" and (to // 8 == 7 or to // 8 == 0):
        b[to] = promo if promo else "Q"
        if color == BLACK:
            b[to] = b[to].lower()
    # castling: move the rook too
    if castling:
        r = 0 if color == WHITE else 7
        if castling == "K":
            b[idx_of(5, r)] = b[idx_of(7, r)]
            b[idx_of(7, r)] = None
            b[idx_of(6, r)] = "K" if color == WHITE else "k"
        else:
            b[idx_of(3, r)] = b[idx_of(0, r)]
            b[idx_of(0, r)] = None
            b[idx_of(2, r)] = "K" if color == WHITE else "k"
    return b


def legal_moves(board, color, castling="KQkq", ep_target=None):
    moves = []
    for frm in range(64):
        piece = board[frm]
        if piece is None or color_of(piece) != color:
            continue
        for m in pseudo_moves(board, frm, ep_target):
            promo = None
            to = m
            if isinstance(m, tuple):
                to, promo = m
            # castling handled below
            nb = apply_move(board, frm, to, promo, ep_target)
            if not in_check(nb, color):
                moves.append((frm, to, promo))
    # castling
    r = 0 if color == WHITE else 7
    if in_check(board, color):
        return moves
    king_idx = idx_of(4, r)
    if board[king_idx] != ("K" if color == WHITE else "k"):
        return moves
    opp = BLACK if color == WHITE else WHITE
    if "K" in castling and (color == WHITE and "K" in castling or
                             color == BLACK and "k" in castling):
        if board[idx_of(5, r)] is None and board[idx_of(6, r)] is None \
                and board[idx_of(7, r)] == ("R" if color == WHITE else "r") \
                and not is_attacked(board, idx_of(5, r), opp) \
                and not is_attacked(board, idx_of(6, r), opp):
            moves.append((king_idx, idx_of(6, r), None, "K"))
    if "Q" in castling and (color == WHITE and "Q" in castling or
                             color == BLACK and "q" in castling):
        if board[idx_of(3, r)] is None and board[idx_of(2, r)] is None \
                and board[idx_of(1, r)] is None \
                and board[idx_of(0, r)] == ("R" if color == WHITE else "r") \
                and not is_attacked(board, idx_of(3, r), opp) \
                and not is_attacked(board, idx_of(2, r), opp):
            moves.append((king_idx, idx_of(2, r), None, "Q"))
    return moves


def game_status(board, color_to_move, castling="KQkq", ep_target=None):
    """Return (is_check, is_checkmate, is_stalemate)."""
    chk = in_check(board, color_to_move)
    moves = legal_moves(board, color_to_move, castling, ep_target)
    if not moves:
        if chk:
            return True, True, False   # checkmate
        return False, False, True       # stalemate
    return chk, False, False


# ── emoji rendering ──────────────────────────────────────────────────────
SYM = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}


def render_board(board, flip=False):
    rows = []
    for r in range(7, -1, -1) if not flip else range(0, 8):
        row = []
        for f in range(8):
            p = board[idx_of(f, r)]
            row.append(SYM.get(p, "·") if p else "·")
        rows.append(" ".join(row))
    return "♟♜♞♝♛♚♝♞♜♟\n" + "\n".join(rows) + f"\n  a b c d e f g h"


def parse_move(text):
    """Parse 'e2e4' or 'e7e8q' into (from_idx, to_idx, promo)."""
    text = text.strip().lower().replace("=", "")
    if len(text) not in (4, 5):
        return None
    frm = parse_square(text[:2])
    to = parse_square(text[2:4])
    if frm is None or to is None:
        return None
    promo = text[4].upper() if len(text) == 5 else None
    if promo and promo not in "QRBN":
        return None
    return frm, to, promo
