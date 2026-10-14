"""Simplified 2-player Nimmt game implementation.

This is a lightweight MVP for local play in the claudecode workspace. The goal is
not to perfectly reproduce the full original rules, but to implement a playable
version with clear board logic, penalty calculation, and local turn-based play.
"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple


COLORS = ["red", "yellow", "green", "black"]


def card_penalty(value: int) -> int:
    """Return card penalty points: 55, repeated-digit multiples of 11, fives, tens, then others."""
    if value == 55:
        return 7
    if value % 11 == 0:
        return 5
    if value % 10 == 5:
        return 3
    if value % 10 == 0:
        return 2
    return 1


def create_deck() -> List[dict]:
    """Create a full 104-card deck with values 1..104, unique IDs, and per-card penalty values."""
    deck = []
    for value in range(1, 105):
        color = COLORS[(value - 1) % len(COLORS)]
        deck.append({
            "value": value,
            "color": color,
            "id": f"{color[0]}-{value}",
            "penalty": card_penalty(value),
        })
    return deck


def choose_best_row(rows: List[List[dict]], card_value: int) -> int:
    """Choose the best row for a card, preferring rows with a max just below it.

    If no row is smaller than the card, the rule becomes: the player chooses an
    existing row to clear and take the penalty, then the card becomes the new first
    card of that row.
    """
    for index, row in enumerate(rows):
        if not row:
            return index

    smaller_rows = []
    for index, row in enumerate(rows):
        max_value = max(card["value"] for card in row)
        if card_value > max_value:
            smaller_rows.append((max_value, index))

    if smaller_rows:
        _, chosen_index = min(smaller_rows, key=lambda item: (card_value - item[0], item[1]))
        return chosen_index

    return 0


def deal_hands(deck: List[dict] | None = None, player_names: List[str] | None = None) -> Dict[str, List[dict]]:
    """Deal 10 unique cards to each of two players and return the remaining deck."""
    if deck is None:
        deck = create_deck()
    if player_names is None:
        player_names = ["Player 1", "Player 2"]

    working_deck = list(deck)
    random.shuffle(working_deck)

    hands = {name: [] for name in player_names}
    for _ in range(10):
        for name in player_names:
            if not working_deck:
                raise ValueError("Not enough cards to deal a full hand.")
            hands[name].append(working_deck.pop())

    for hand in hands.values():
        hand.sort(key=lambda c: c["value"])

    return hands


def place_card(rows: List[List[dict]], card: dict, row_index: int) -> Tuple[List[List[dict]], int]:
    """Place a card using the corrected Nimmt rule.

    If the card is smaller than or equal to the chosen row's maximum value, the
    player clears that row and takes its penalty before placing the card as the new
    first card in that row. Otherwise, the card is appended normally.
    """
    if not 0 <= row_index < len(rows):
        raise IndexError(f"Row index out of range: {row_index}")

    updated_rows = [list(row) for row in rows]
    target_row = updated_rows[row_index]

    if not target_row:
        target_row.append(card)
        return updated_rows, 0

    if card["value"] <= max(item["value"] for item in target_row):
        penalty = sum(item["penalty"] for item in target_row)
        updated_rows[row_index] = [card]
        return updated_rows, penalty

    target_row.append(card)
    if len(target_row) >= 6:
        penalty = sum(item["penalty"] for item in target_row[:-1])
        updated_rows[row_index] = [card]
        return updated_rows, penalty

    return updated_rows, 0


def create_initial_game_state(player_names: List[str] | None = None) -> Dict[str, object]:
    """Build a fresh game state with a chosen number of players."""
    if player_names is None:
        player_names = ["Player 1", "Player 2"]

    player_names = list(player_names)
    deck = create_deck()
    starting_cards = random.sample(deck, 4)
    remaining = [card for card in deck if card["id"] not in {item["id"] for item in starting_cards}]

    hands = deal_hands(deck=remaining, player_names=player_names)
    remaining_after_deals = list(remaining)
    for hand in hands.values():
        for card in hand:
            if card in remaining_after_deals:
                remaining_after_deals.remove(card)

    rows = [[card] for card in starting_cards]
    player_types = {name: "human" for name in player_names}
    return {
        "players": list(player_names),
        "player_count": len(player_names),
        "player_types": player_types,
        "hands": hands,
        "rows": rows,
        "deck": remaining_after_deals,
        "draw_pile": remaining_after_deals,
        "penalties": {name: 0 for name in player_names},
        "round_penalties": {name: 0 for name in player_names},
        "wins": {name: 0 for name in player_names},
        "current_player": 0,
        "round": 1,
        "turn": 1,
        "round_over": False,
        "winner": None,
        "winners": [],
    }


def draw_from_deck(game: Dict[str, object], count: int = 1) -> List[dict]:
    """Draw cards from the draw pile and return the drawn cards."""
    pile = game["draw_pile"]
    drawn = []
    for _ in range(count):
        if not pile:
            break
        drawn.append(pile.pop())
    return drawn


def process_selected_cards(
    game: Dict[str, object],
    player1_name: str,
    player1_value: int,
    player2_name: str,
    player2_value: int,
    player1_row: int | None = None,
    player2_row: int | None = None,
) -> Dict[str, object]:
    """Process two selected cards in numeric order, as in the revised Nimmt rules.

    The round flow is: player 1 chooses, player 2 chooses, then cards are resolved
    from the lower value to the higher value. Any card smaller than or equal to the
    row's current max forces the row to be cleared and the player keeps the card as
    the first card of that row.
    """
    if game["round_over"]:
        raise ValueError("The round is already over.")

    player_cards = []
    for player_name, value, row_index in [
        (player1_name, player1_value, player1_row),
        (player2_name, player2_value, player2_row),
    ]:
        hand = game["hands"][player_name]
        candidate = next((card for card in hand if card["value"] == value), None)
        if candidate is None:
            raise ValueError(f"Card {value} is not in {player_name}'s hand.")
        player_cards.append((player_name, candidate, row_index))

    ordered = sorted(player_cards, key=lambda item: item[1]["value"])
    placed_cards = []
    penalty_total = 0
    step_logs = []

    game.setdefault("round_penalties", {name: 0 for name in game["players"]})

    for player_name, card, row_index in ordered:
        if row_index is None:
            row_index = choose_best_row(game["rows"], card["value"])
        if not 0 <= row_index < len(game["rows"]):
            raise IndexError(f"Row index out of range: {row_index}")

        updated_rows, penalty = place_card(game["rows"], card, row_index)
        game["rows"] = updated_rows
        game["hands"][player_name].remove(card)
        game["penalties"][player_name] += penalty
        game["round_penalties"][player_name] = game["round_penalties"].get(player_name, 0) + penalty
        penalty_total += penalty
        placed_cards.append(card["value"])
        step_logs.append({
            "player": player_name,
            "card": card,
            "row": row_index,
            "penalty": penalty,
        })

    game["round_over"] = any(not game["hands"][name] for name in (player1_name, player2_name))
    if game["round_over"]:
        finish_round(game)

    return {
        "placed_cards": placed_cards,
        "penalty_total": penalty_total,
        "rows": game["rows"],
        "penalties": game["penalties"],
        "round_over": game["round_over"],
        "winner": game["winner"],
        "steps": step_logs,
    }


def play_turn(game: Dict[str, object], player_name: str, card_value: int, row_index: int | None = None) -> int:
    """Backward-compatible single-player turn handling."""
    if game["round_over"]:
        raise ValueError("The round is already over.")

    hand = game["hands"][player_name]
    candidate = next((card for card in hand if card["value"] == card_value), None)
    if candidate is None:
        raise ValueError(f"Card {card_value} is not in {player_name}'s hand.")

    rows = game["rows"]
    if row_index is None:
        row_index = choose_best_row(rows, card_value)

    updated_rows, penalty = place_card(rows, candidate, row_index)
    game["rows"] = updated_rows
    game["hands"][player_name].remove(candidate)
    game["penalties"][player_name] += penalty
    game.setdefault("round_penalties", {name: 0 for name in game["players"]})
    game["round_penalties"][player_name] = game["round_penalties"].get(player_name, 0) + penalty

    if not game["hands"][player_name]:
        game["round_over"] = True
        finish_round(game)

    return penalty


def determine_winner(penalties: Dict[str, int]) -> str:
    """Return the player with the lowest penalty total."""
    return min(penalties.items(), key=lambda item: item[1])[0]


def determine_round_winners(round_penalties: Dict[str, int]) -> List[str]:
    """Return every player tied for the lowest penalty in the round, in player order."""
    lowest = min(round_penalties.values())
    return [name for name, penalty in round_penalties.items() if penalty == lowest]


def finish_round(game: Dict[str, object]) -> List[str]:
    """Record the round winners and credit each of them with one win.

    Winners are decided by this round's penalty only, so tied players all win.
    ``winner`` keeps the first winner for callers that expect a single name.
    """
    round_penalties = {name: game["round_penalties"].get(name, 0) for name in game["players"]}
    winners = determine_round_winners(round_penalties)
    wins = game.setdefault("wins", {name: 0 for name in game["players"]})
    for name in winners:
        wins[name] = wins.get(name, 0) + 1
    game["winners"] = winners
    game["winner"] = winners[0]
    return winners


def format_card(card: dict) -> str:
    return f"{card['value']}{card['color'][0].upper()}"


def format_card_detail(card: dict) -> str:
    return f"[{card['value']:>3}{card['color'][0].upper()}:{card['penalty']}]"


def render_board(rows: List[List[dict]]) -> str:
    """Render the card rows with their penalty totals and six-card warnings."""
    lines = [
        "BOARD",
        "Cards: [value + color : penalty] | rows clear when a 6th card is played",
    ]
    for index, row in enumerate(rows, start=1):
        cards = " ".join(format_card_detail(card) for card in row) or "[ empty ]"
        penalty = sum(card["penalty"] for card in row)
        max_value = row[-1]["value"] if row else "-"
        cards_until_clear = max(0, 6 - len(row))
        if cards_until_clear <= 1:
            status = "NEXT CARD CLEARS"
        else:
            status = f"{cards_until_clear} cards to clear"
        lines.append(
            f"Row {index} | {cards} | max {max_value} | {len(row)}/5 | "
            f"take {penalty} pts | {status}"
        )
    return "\n".join(lines)


def show_board(rows: List[List[dict]]) -> None:
    print(render_board(rows))


def render_scoreboard(
    players: List[str],
    penalties: Dict[str, int],
    round_penalties: Dict[str, int],
    hands: Dict[str, List[dict]],
    wins: Dict[str, int] | None = None,
) -> str:
    """Render current standings, with a comparable bar for accumulated penalties."""
    if wins is None:
        wins = {}
    ranked_players = sorted(players, key=lambda name: penalties[name])
    highest_penalty = max((penalties[name] for name in players), default=0)
    bar_width = 12
    lines = ["SCOREBOARD (lower penalty is better)"]

    for rank, name in enumerate(ranked_players, start=1):
        total = penalties[name]
        filled = round(total / highest_penalty * bar_width) if highest_penalty else 0
        bar = "#" * filled + "." * (bar_width - filled)
        lines.append(
            f"{rank:>2}. {name} | round {round_penalties.get(name, 0)} "
            f"| total {total} | wins {wins.get(name, 0)} | hand {len(hands[name])} | {bar}"
        )
    return "\n".join(lines)


def describe_turn_result(player_name: str, card: dict, row_index: int, penalty: int) -> str:
    """Return a readable description of what happened in a turn."""
    if penalty > 0:
        return (
            f"{player_name} played {card['value']} on Row {row_index + 1}, "
            f"cleared that row, and took {penalty} penalty points."
        )
    return f"{player_name} played {card['value']} on Row {row_index + 1}."


def show_round_result(game: Dict[str, object]) -> None:
    """Display each player's round penalty, cumulative total, and wins after the round ends."""
    print("\n=== Round Result ===")
    for name in game["players"]:
        round_penalty = game.get("round_penalties", {}).get(name, 0)
        total_penalty = game["penalties"].get(name, 0)
        wins = game.get("wins", {}).get(name, 0)
        print(f"  {name}: this round = {round_penalty}, total = {total_penalty}, wins = {wins}")
    winners = game.get("winners") or []
    if winners:
        round_penalty = game.get("round_penalties", {}).get(winners[0], 0)
        print(f"Round winner: {' & '.join(winners)} with {round_penalty} penalty points this round.")


def prepare_next_round(previous_game: Dict[str, object]) -> Dict[str, object]:
    """Start the next round while carrying penalties and player roles forward."""
    next_game = create_initial_game_state(previous_game["players"])
    next_game["penalties"] = dict(previous_game["penalties"])
    next_game["round_penalties"] = {name: 0 for name in previous_game["players"]}
    next_game["wins"] = dict(previous_game.get("wins", {name: 0 for name in previous_game["players"]}))
    next_game["winners"] = []
    next_game["round"] = int(previous_game["round"]) + 1
    next_game["turn"] = 1
    next_game["round_over"] = False
    next_game["winner"] = None
    next_game["player_count"] = int(previous_game.get("player_count", len(previous_game["players"])))
    next_game["player_types"] = dict(previous_game.get("player_types", {name: "human" for name in previous_game["players"]}))
    return next_game


def prompt_player_count() -> int:
    """Ask the user how many players will participate."""
    while True:
        try:
            count = int(input("How many players? (2-8): ").strip())
        except ValueError:
            print("Please enter a valid integer.")
            continue
        if 2 <= count <= 8:
            return count
        print("Player count must be between 2 and 8.")


def prompt_player_types(player_names: List[str]) -> Dict[str, str]:
    """Ask the user to choose a type for each player."""
    choices: Dict[str, str] = {}
    for name in player_names:
        while True:
            choice = input(f"{name}: choose player type [human/ai/random]: ").strip().lower()
            if choice in {"human", "h"}:
                choices[name] = "human"
                break
            if choice in {"ai", "a"}:
                choices[name] = "ai"
                break
            if choice in {"random", "r"}:
                choices[name] = "random"
                break
            print("Please choose one of: human, ai, random")
    return choices


def resolve_auto_player_action(game: Dict[str, object], player_name: str, player_type: str) -> Dict[str, object]:
    """Choose only the card for a non-human player.

    Row selection is deferred until the same decision point used for human players,
    and only happens when the chosen card requires a row-clear decision.
    """
    hand = game["hands"][player_name]
    if not hand:
        raise ValueError(f"{player_name} has no cards left.")

    if player_type == "random":
        card = random.choice(hand)
        return {"card_value": card["value"], "card": card}

    best_card = max(hand, key=lambda item: item["value"])
    return {"card_value": best_card["value"], "card": best_card}


def play_game() -> None:
    """Run CLI rounds with a configurable number of players and accumulated penalties."""
    player_count = prompt_player_count()
    names = [f"Player {index}" for index in range(1, player_count + 1)]
    game = create_initial_game_state(names)
    game["round"] = 1
    game["turn"] = 1
    game["player_count"] = player_count
    game["player_types"] = prompt_player_types(names)

    print("=== Nimmt (Revised Multi-Player Rules) ===")
    print("Rules: each player chooses a card, then the lower card is resolved first.")
    print("If a card is not larger than the row's max, the player clears that row and takes the penalty.")
    print("If a row reaches 6 cards, the player who placed the 6th card takes the previous 5-card penalty.")
    print("Input is shown as: card value, then row number. The system also explains what happened.")

    while True:
        print(f"\nRound {game['round']} / Turn {game['turn']}")
        print(render_scoreboard(
            names,
            game["penalties"],
            game["round_penalties"],
            game["hands"],
            game["wins"],
        ))
        print("HANDS (card penalty shown after ':'):")
        for name in names:
            hand = " ".join(format_card_detail(card) for card in game["hands"][name])
            print(f"  {name}: {hand}")
        show_board(game["rows"])

        selected = []
        for name in names:
            player_type = game["player_types"].get(name, "human")
            if player_type == "human":
                while True:
                    try:
                        value = int(input(f"{name} choose a card value: ").strip())
                    except ValueError:
                        print("Please enter integer values only.")
                        continue
                    if not any(card["value"] == value for card in game["hands"][name]):
                        print(f"{name} does not have card {value}.")
                        continue
                    selected.append((name, value, None))
                    break
                continue

            action = resolve_auto_player_action(game, name, player_type)
            selected.append((name, action["card_value"], None))

        print("\nTurn flow: all players chose cards.")
        print("Turn flow: cards are resolved in ascending numeric order automatically.")
        print("Turn flow: if a card is smaller than or equal to a row's max, the player must clear that row.")

        ordered = sorted(selected, key=lambda item: item[1])
        result_steps = []

        for player_name, card_value, preselected_row in ordered:
            hand = game["hands"][player_name]
            card = next(card for card in hand if card["value"] == card_value)
            row_index = preselected_row
            if row_index is None:
                has_lower_row = any(
                    (not row) or max(item["value"] for item in row) < card_value
                    for row in game["rows"]
                )

                if has_lower_row:
                    row_index = choose_best_row(game["rows"], card_value)
                else:
                    row_candidates = [
                        index for index, row in enumerate(game["rows"])
                        if row and max(item["value"] for item in row) >= card_value
                    ]
                    row_prompt = (
                        f"{player_name}: this card is not larger than a row's max, so this card must clear a row. "
                        f"Choose a row index (1-{len(game['rows'])}) to clear: "
                    )
                    while True:
                        try:
                            chosen_row = int(input(row_prompt).strip()) - 1
                        except ValueError:
                            print("Please enter an integer row number.")
                            continue
                        if chosen_row in row_candidates:
                            row_index = chosen_row
                            break
                        print("That row is not valid for this card. Choose a row whose current max is greater than or equal to the card.")

            updated_rows, penalty = place_card(game["rows"], card, row_index)
            game["rows"] = updated_rows
            game["hands"][player_name].remove(card)
            game["penalties"][player_name] += penalty
            game["round_penalties"][player_name] = game["round_penalties"].get(player_name, 0) + penalty
            result_steps.append({
                "player": player_name,
                "card": card,
                "row": row_index,
                "penalty": penalty,
            })

        game["round_over"] = any(not game["hands"][name] for name in names)
        if game["round_over"]:
            finish_round(game)

        total_penalty = sum(step['penalty'] for step in result_steps)
        print(f"Turn summary: total penalty for this turn = {total_penalty}")
        print("Round result summary")
        for step in result_steps:
            print(describe_turn_result(step["player"], step["card"], step["row"], step["penalty"]))
        print("Round summary: the round is complete. The penalties remain accumulated for the next round.")
        print(render_scoreboard(
            names,
            game["penalties"],
            game["round_penalties"],
            game["hands"],
            game["wins"],
        ))
        show_board(game["rows"])

        if game["round_over"]:
            show_round_result(game)
            print(f"Round over. Winner of this round: {' & '.join(game['winners'])}.")
            while True:
                choice = input("Continue to the next round? (y/n): ").strip().lower()
                if choice in {"y", "yes"}:
                    game = prepare_next_round(game)
                    break
                if choice in {"n", "no"}:
                    return
                print("Please answer y/yes or n/no.")
            continue

        game["turn"] += 1


if __name__ == "__main__":
    play_game()
