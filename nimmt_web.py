"""Local web application adapter for the Nimmt game."""

from __future__ import annotations

import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nimmt_online import OnlineRoomManager
from nimmt import (
    choose_best_row,
    create_initial_game_state,
    finish_round,
    place_card,
    prepare_next_round,
)


WEB_ROOT = Path(__file__).resolve().parent / "web"
MAX_REQUEST_BYTES = 64 * 1024


class NimmtWebApp:
    """Own one in-memory game and expose safe, turn-scoped views of its state."""

    def __init__(self) -> None:
        self.game: dict[str, Any] | None = None
        self.pending: list[tuple[str, int]] = []
        self.selection_cursor = 0
        self.awaiting_handoff = False
        self.phase = "setup"
        self.active_player: str | None = None
        self.row_candidates: list[int] = []
        self.played_cards: list[dict[str, Any]] = []
        self.last_results: list[dict[str, Any]] = []

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        names = payload.get("players")
        player_types = payload.get("player_types")
        if not isinstance(names, list) or not 2 <= len(names) <= 8:
            raise ValueError("プレイヤーは2〜8人で設定してください。")
        if any(
            not isinstance(name, str) or not name.strip() or len(name.strip()) > 24
            for name in names
        ):
            raise ValueError("プレイヤー名は1〜24文字で入力してください。")
        normalized_names = [name.strip() for name in names]
        if len(set(normalized_names)) != len(normalized_names):
            raise ValueError("プレイヤー名は重複できません。")
        if not isinstance(player_types, dict) or set(player_types) != set(normalized_names):
            raise ValueError("各プレイヤーのタイプを指定してください。")
        if any(
            not isinstance(player_types[name], str)
            or player_types[name] not in {"human", "ai", "random"}
            for name in normalized_names
        ):
            raise ValueError("プレイヤータイプが正しくありません。")

        self.game = create_initial_game_state(normalized_names)
        self.game["player_types"] = {name: player_types[name] for name in normalized_names}
        self.played_cards = []
        self.last_results = []
        self._begin_turn()
        return self.state()

    def state(self) -> dict[str, Any]:
        if self.game is None:
            return {"phase": "setup"}

        game = self.game
        current_name = self.active_player
        current_type = (
            game["player_types"].get(current_name, "human")
            if current_name is not None
            else None
        )
        can_see_hand = (
            self.phase == "choose"
            and not self.awaiting_handoff
            and current_name is not None
            and current_type == "human"
        )
        visible_hand = (
            [
                {
                    "value": card["value"],
                    "color": card["color"],
                    "penalty": card["penalty"],
                }
                for card in game["hands"][current_name]
            ]
            if can_see_hand
            else None
        )

        return {
            "phase": self.phase,
            "round": game["round"],
            "turn": game["turn"],
            "players": [
                {
                    "name": name,
                    "type": game["player_types"].get(name, "human"),
                    "penalty": game["penalties"][name],
                    "round_penalty": game["round_penalties"].get(name, 0),
                    "wins": game["wins"].get(name, 0),
                    "hand_count": len(game["hands"][name]),
                }
                for name in game["players"]
            ],
            "rows": [
                {
                    "cards": [
                        {
                            "value": card["value"],
                            "color": card["color"],
                            "penalty": card["penalty"],
                        }
                        for card in row
                    ],
                    "max": max((card["value"] for card in row), default=None),
                    "penalty": sum(card["penalty"] for card in row),
                }
                for row in game["rows"]
            ],
            "active_player": current_name,
            "active_type": current_type,
            "awaiting_handoff": self.awaiting_handoff,
            "hand": visible_hand,
            "selected_count": len(self.pending),
            "selection_count": len(game["players"]),
            "resolution_card": self._visible_resolution_card(),
            "row_candidates": list(self.row_candidates),
            "played_cards": list(self.played_cards),
            "last_results": list(self.last_results),
            "winner": game.get("winner"),
            "winners": list(game.get("winners", [])),
        }

    def submit_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        game = self._require_game()
        if self.phase != "choose" or self.awaiting_handoff:
            raise ValueError("今はカードを選べません。")
        player = self.active_player
        if player is None or game["player_types"].get(player) != "human":
            raise ValueError("このプレイヤーはカードを選択できません。")

        value = payload.get("card_value")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("カードの数字を選んでください。")
        hand = game["hands"][player]
        if not any(card["value"] == value for card in hand):
            raise ValueError(f"{player}の手札にカード{value}はありません。")

        self.pending.append((player, value))
        self.selection_cursor += 1
        self._continue_turn()
        return self.state()

    def acknowledge_handoff(self) -> dict[str, Any]:
        if self.phase not in {"choose", "row"} or not self.awaiting_handoff:
            raise ValueError("受け渡しの確認は必要ありません。")
        self.awaiting_handoff = False
        return self.state()

    def choose_row(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.phase != "row":
            raise ValueError("今は列を選べません。")
        row_index = payload.get("row_index")
        if not isinstance(row_index, int) or isinstance(row_index, bool):
            raise ValueError("列を選んでください。")
        if row_index not in self.row_candidates:
            raise ValueError("その列は選択できません。")

        self._place_pending_card(row_index)
        self._continue_resolution()
        return self.state()

    def next_round(self) -> dict[str, Any]:
        game = self._require_game()
        if self.phase != "round_over":
            raise ValueError("ラウンド終了後に次のラウンドへ進めます。")
        self.game = prepare_next_round(game)
        self.played_cards = []
        self.last_results = []
        self._begin_turn()
        return self.state()

    def reset(self) -> dict[str, Any]:
        self.game = None
        self.pending = []
        self.selection_cursor = 0
        self.awaiting_handoff = False
        self.phase = "setup"
        self.active_player = None
        self.row_candidates = []
        self.played_cards = []
        self.last_results = []
        return self.state()

    def _require_game(self) -> dict[str, Any]:
        if self.game is None:
            raise ValueError("先にゲームを開始してください。")
        return self.game

    def _begin_turn(self) -> None:
        self.phase = "choose"
        self.pending = []
        self.selection_cursor = 0
        self.awaiting_handoff = False
        self.active_player = None
        self.row_candidates = []
        self._continue_turn()

    def _continue_turn(self) -> None:
        game = self._require_game()
        while self.selection_cursor < len(game["players"]):
            player = game["players"][self.selection_cursor]
            player_type = game["player_types"].get(player, "human")
            if player_type == "human":
                self.active_player = player
                self.awaiting_handoff = True
                return
            hand = game["hands"][player]
            if player_type == "random":
                card = random.choice(hand)
            else:
                card = max(hand, key=lambda item: item["value"])
            self.pending.append((player, card["value"]))
            self.selection_cursor += 1

        self.pending.sort(key=lambda item: item[1])
        self.played_cards = [
            {
                "player": player,
                "card_value": value,
                "color": next(
                    card["color"]
                    for card in game["hands"][player]
                    if card["value"] == value
                ),
                "penalty": next(
                    card["penalty"]
                    for card in game["hands"][player]
                    if card["value"] == value
                ),
            }
            for player, value in self.pending
        ]
        self.last_results = []
        self.phase = "resolve"
        self.active_player = None
        self.awaiting_handoff = False
        self._continue_resolution()

    def _continue_resolution(self) -> None:
        game = self._require_game()
        while self.pending:
            player, value = self.pending[0]
            card = next(card for card in game["hands"][player] if card["value"] == value)
            lower_rows = [
                index
                for index, row in enumerate(game["rows"])
                if not row or max(item["value"] for item in row) < value
            ]
            if lower_rows:
                self._place_pending_card(choose_best_row(game["rows"], value))
                continue

            candidates = [
                index
                for index, row in enumerate(game["rows"])
                if row and max(item["value"] for item in row) >= value
            ]
            player_type = game["player_types"].get(player, "human")
            if player_type == "human":
                self.phase = "row"
                self.active_player = player
                self.row_candidates = candidates
                self.awaiting_handoff = True
                return

            if player_type == "random":
                chosen_row = random.choice(candidates)
            else:
                chosen_row = min(
                    candidates,
                    key=lambda index: (
                        sum(card["penalty"] for card in game["rows"][index]),
                        index,
                    ),
                )
            self._place_pending_card(chosen_row)

        game["round_over"] = any(not game["hands"][name] for name in game["players"])
        if game["round_over"]:
            finish_round(game)
            self.phase = "round_over"
            self.active_player = None
            self.row_candidates = []
            return

        game["turn"] += 1
        self._begin_turn()

    def _place_pending_card(self, row_index: int) -> None:
        game = self._require_game()
        player, value = self.pending.pop(0)
        card = next(card for card in game["hands"][player] if card["value"] == value)
        previous_row = list(game["rows"][row_index])
        sixth_card = (
            len(previous_row) == 5
            and max(item["value"] for item in previous_row) < value
        )
        took_existing_row = bool(previous_row) and value <= max(
            item["value"] for item in previous_row
        )
        game["rows"], penalty = place_card(game["rows"], card, row_index)
        game["hands"][player].remove(card)
        game["penalties"][player] += penalty
        game["round_penalties"][player] = game["round_penalties"].get(player, 0) + penalty
        self.last_results.append(
            {
                "player": player,
                "card_value": value,
                "row": row_index,
                "penalty": penalty,
                "collection_reason": (
                    "sixth_card"
                    if sixth_card
                    else "player_choice"
                    if took_existing_row
                    else None
                ),
                "collected_cards": [
                    {
                        "value": item["value"],
                        "color": item["color"],
                        "penalty": item["penalty"],
                    }
                    for item in previous_row
                    if sixth_card or took_existing_row
                ],
            }
        )
        self.row_candidates = []
        self.active_player = None
        self.phase = "resolve"

    def _visible_resolution_card(self) -> dict[str, Any] | None:
        if self.phase != "row" or not self.pending:
            return None
        player, value = self.pending[0]
        return {
            "player": player,
            "value": value,
            "type": self._require_game()["player_types"].get(player, "human"),
        }


class NimmtRequestHandler(BaseHTTPRequestHandler):
    """Serve the static game client and a small JSON API."""

    app = NimmtWebApp()
    rooms = OnlineRoomManager()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._send_json(200, self.app.state())
            return
        if path == "/api/online/state":
            self.send_error(405, "Use POST with the private player token.")
            return
        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        entry = static_files.get(path)
        if entry is None:
            self.send_error(404)
            return
        filename, content_type = entry
        content = (WEB_ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        routes = {
            "/api/start": self.app.start,
            "/api/choose-card": self.app.submit_card,
            "/api/acknowledge-handoff": lambda payload: self.app.acknowledge_handoff(),
            "/api/choose-row": self.app.choose_row,
            "/api/next-round": lambda payload: self.app.next_round(),
            "/api/reset": lambda payload: self.app.reset(),
            "/api/online/create": lambda payload: self.rooms.create_room(
                payload.get("name"), payload.get("max_players", 8)
            ),
            "/api/online/join": lambda payload: self.rooms.join_room(
                payload.get("room_code"), payload.get("name")
            ),
            "/api/online/state": lambda payload: self.rooms.state(
                payload.get("token")
            ),
            "/api/online/bot": lambda payload: self.rooms.add_bot(
                payload.get("token"), payload.get("player_type")
            ),
            "/api/online/ready": lambda payload: self.rooms.set_ready(
                payload.get("token"), payload.get("ready", True)
            ),
            "/api/online/start": lambda payload: self.rooms.start_game(
                payload.get("token")
            ),
            "/api/online/choose-card": lambda payload: self.rooms.choose_card(
                payload.get("token"), payload.get("card_value")
            ),
            "/api/online/choose-row": lambda payload: self.rooms.choose_row(
                payload.get("token"), payload.get("row_index")
            ),
            "/api/online/next-round": lambda payload: self.rooms.next_round(
                payload.get("token")
            ),
        }
        action = routes.get(urlparse(self.path).path)
        if action is None:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise ValueError("リクエストのサイズが正しくありません。")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("リクエスト形式が正しくありません。")
            self._send_json(200, action(payload))
        except ValueError as error:
            self._send_json(400, {"error": str(error)})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _add_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'",
        )

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8000), NimmtRequestHandler)
    print("Nimmt Web UI: http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Nimmt Web UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
