"""
Iota Bot — Generic Tournament Bracket (single-elimination)

Pure bracket logic (no Telegram / DB) so it can be unit-tested. The handler
(handlers/tournament.py) stores state in MongoDB and drives it via buttons.

build_bracket(participants) -> list of rounds; round[r] is a list of matches.
A match is {"p1": id, "p2": id|None, "winner": id|None}. A None p2 is a bye
(auto-advance p1 to the next round).
"""
import random


def build_bracket(participants):
    players = list(participants)
    random.shuffle(players)
    # pad to next power of two with byes (represented as None opponents)
    n = 1
    while n < len(players):
        n *= 2
    while len(players) < n:
        players.append(None)
    rounds = []
    round0 = []
    for i in range(0, len(players), 2):
        round0.append({"p1": players[i], "p2": players[i + 1], "winner": None})
    rounds.append(round0)
    # pre-create later rounds (winners filled as matches resolve)
    r = round0
    while len(r) > 1:
        nxt = [{"p1": None, "p2": None, "winner": None} for _ in range(len(r) // 2)]
        rounds.append(nxt)
        r = nxt
    return rounds


def current_matches(rounds):
    """The first round that still has an unresolved match."""
    for r in rounds:
        for m in r:
            if m["winner"] is None:
                return r
    return None


def report_winner(rounds, match_index, winner):
    """Record a winner for `match_index` in the current round and advance
    them into the next round's corresponding slot. Returns True if a slot
    was filled."""
    cur = current_matches(rounds)
    if cur is None:
        return False
    if not (0 <= match_index < len(cur)):
        return False
    m = cur[match_index]
    if winner not in (m["p1"], m["p2"]):
        return False
    if m["winner"] is not None:
        return False
    m["winner"] = winner
    # find which round we're in and place into next round
    ri = rounds.index(cur)
    if ri + 1 < len(rounds):
        nxt = rounds[ri + 1]
        slot = match_index // 2
        if match_index % 2 == 0:
            nxt[slot]["p1"] = winner
        else:
            nxt[slot]["p2"] = winner
    return True


def champion(rounds):
    if current_matches(rounds) is None and rounds:
        last = rounds[-1][0]
        return last["winner"]
    return None
