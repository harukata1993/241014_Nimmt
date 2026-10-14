"""Room management and private player views for online Nimmt games."""

from __future__ import annotations

import secrets
import string
import threading
import time
from typing import Any

from nimmt import (
    choose_best_row,
    create_initial_game_state,
    finish_round,
    place_card,
    prepare_next_round,
)


ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_TTL_SECONDS = 2 * 60 * 60
MAX_PLAYERS = 8
MIN_PLAYERS = 2
MAX_ROOMS = 128


class OnlineRoomManager:
    """Manage temporary rooms; all state changes are serialized by one lock."""

    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, tuple[str, str]] = {}
        self.lock = threading.RLock()

    def create_room(self, player_name: Any, max_players: Any = 8) -> dict[str, Any]:
        name = self._validate_name(player_name)
        if not isinstance(max_players, int) or isinstance(max_players, bool):
            raise ValueError("人数は2〜8人で設定してください。")
        if not MIN_PLAYERS <= max_players <= MAX_PLAYERS:
            raise ValueError("人数は2〜8人で設定してください。")

        with self.lock:
            self._expire_rooms()
            if len(self.rooms) >= MAX_ROOMS:
                raise ValueError("現在、作成できる部屋数の上限に達しています。")
            room_code = self._new_room_code()
            host_token = self._new_token()
            room = {
                "code": room_code,
                "host": name,
                "max_players": max_players,
                "seats": [
                    {
                        "name": name,
                        "type": "human",
                        "token": host_token,
                        "ready": True,
                    }
                ],
                "game": None,
                "phase": "lobby",
                "turn": 1,
                "round": 1,
                "selected": {},
                "to_resolve": [],
                "active_player": None,
                "row_candidates": [],
                "winner": None,
                "winners": [],
                "played_cards": [],
                "last_results": [],
                "last_activity": time.monotonic(),
                "revision": 0,
            }
            self.rooms[room_code] = room
            self.tokens[host_token] = (room_code, name)
            return {"token": host_token, **self._state(room, name)}

    def join_room(self, room_code: Any, player_name: Any) -> dict[str, Any]:
        if not isinstance(room_code, str):
            raise ValueError("部屋コードが正しくありません。")
        code = room_code.strip().upper()
        name = self._validate_name(player_name)
        with self.lock:
            self._expire_rooms()
            room = self.rooms.get(code)
            if room is None or room["phase"] != "lobby":
                raise ValueError("参加できる部屋が見つかりません。コードを確認してください。")
            if any(seat["name"].casefold() == name.casefold() for seat in room["seats"]):
                raise ValueError("その表示名はこの部屋ですでに使われています。")
            if len(room["seats"]) >= room["max_players"]:
                raise ValueError("この部屋は満員です。")

            token = self._new_token()
            room["seats"].append(
                {"name": name, "type": "human", "token": token, "ready": False}
            )
            room["last_activity"] = time.monotonic()
            room["revision"] += 1
            self.tokens[token] = (code, name)
            return {"token": token, **self._state(room, name)}

    def add_bot(self, token: Any, player_type: Any, name: Any = None) -> dict[str, Any]:
        if not isinstance(player_type, str) or player_type not in {"ai", "random"}:
            raise ValueError("ボットはAIまたはランダムから選んでください。")
        with self.lock:
            room, owner = self._room_for_token(token)
            if room["phase"] != "lobby" or owner != room["host"]:
                raise ValueError("部屋主のみロビーでボットを追加できます。")
            if len(room["seats"]) >= room["max_players"]:
                raise ValueError("この部屋は満員です。")
            if name is None:
                index = 1
                while any(seat["name"] == f"{player_type.upper()} {index}" for seat in room["seats"]):
                    index += 1
                bot_name = f"{player_type.upper()} {index}"
            else:
                bot_name = self._validate_name(name)
            if any(seat["name"].casefold() == bot_name.casefold() for seat in room["seats"]):
                raise ValueError("その表示名はこの部屋ですでに使われています。")
            room["seats"].append(
                {"name": bot_name, "type": player_type, "token": None, "ready": True}
            )
            self._touch(room)
            return self._state(room, owner)

    def set_ready(self, token: Any, ready: Any = True) -> dict[str, Any]:
        if not isinstance(ready, bool):
            raise ValueError("準備状態が正しくありません。")
        with self.lock:
            room, player = self._room_for_token(token)
            if room["phase"] != "lobby":
                raise ValueError("ロビーでのみ準備状態を変更できます。")
            seat = self._seat(room, player)
            seat["ready"] = ready
            self._touch(room)
            return self._state(room, player)

    def start_game(self, token: Any) -> dict[str, Any]:
        with self.lock:
            room, player = self._room_for_token(token)
            if room["phase"] != "lobby" or player != room["host"]:
                raise ValueError("部屋主のみゲームを開始できます。")
            if len(room["seats"]) < MIN_PLAYERS:
                raise ValueError("ゲーム開始には2人以上必要です。")
            if any(
                seat["type"] == "human" and not seat["ready"]
                for seat in room["seats"]
            ):
                raise ValueError("全員が準備完了してから開始してください。")
            self._start_round(room, first_round=True)
            self._touch(room)
            return self._state(room, player)

    def state(self, token: Any) -> dict[str, Any]:
        with self.lock:
            room, player = self._room_for_token(token)
            room["last_activity"] = time.monotonic()
            return self._state(room, player)

    def choose_card(self, token: Any, card_value: Any) -> dict[str, Any]:
        with self.lock:
            room, player = self._room_for_token(token)
            if room["phase"] != "choose":
                raise ValueError("今はカードを選べません。")
            seat = self._seat(room, player)
            if seat["type"] != "human":
                raise ValueError("人間プレイヤーのみカードを選べます。")
            if player in room["selected"]:
                raise ValueError("このターンのカードはすでに選択済みです。")
            if not isinstance(card_value, int) or isinstance(card_value, bool):
                raise ValueError("カードの数字が正しくありません。")
            hand = room["game"]["hands"][player]
            if not any(card["value"] == card_value for card in hand):
                raise ValueError("そのカードは手札にありません。")

            room["selected"][player] = card_value
            self._begin_resolution_if_ready(room)
            self._touch(room)
            return self._state(room, player)

    def choose_row(self, token: Any, row_index: Any) -> dict[str, Any]:
        with self.lock:
            room, player = self._room_for_token(token)
            if room["phase"] != "row" or room["active_player"] != player:
                raise ValueError("このプレイヤーは今、列を選べません。")
            if not isinstance(row_index, int) or isinstance(row_index, bool):
                raise ValueError("列の番号が正しくありません。")
            if row_index not in room["row_candidates"]:
                raise ValueError("その列は選択できません。")
            self._place_current(room, row_index)
            self._continue_resolution(room)
            self._touch(room)
            return self._state(room, player)

    def next_round(self, token: Any) -> dict[str, Any]:
        with self.lock:
            room, player = self._room_for_token(token)
            if room["phase"] != "round_over":
                raise ValueError("ラウンド終了後に進めます。")
            if player != room["host"]:
                raise ValueError("部屋主のみ次のラウンドを開始できます。")
            room["game"] = prepare_next_round(room["game"])
            room["played_cards"] = []
            room["last_results"] = []
            self._start_round(room, first_round=False)
            self._touch(room)
            return self._state(room, player)

    def _state(self, room: dict[str, Any], viewer: str) -> dict[str, Any]:
        is_host = viewer == room["host"]
        state: dict[str, Any] = {
            "room_code": room["code"],
            "revision": room["revision"],
            "phase": room["phase"],
            "is_host": is_host,
            "host_name": room["host"],
            "viewer": viewer,
            "max_players": room["max_players"],
            "players": [
                {
                    "name": seat["name"],
                    "type": seat["type"],
                    "ready": seat["ready"],
                    "is_host": seat["name"] == room["host"],
                }
                for seat in room["seats"]
            ],
        }
        game = room["game"]
        if game is None:
            state["can_start"] = (
                is_host
                and len(room["seats"]) >= MIN_PLAYERS
                and all(
                    seat["type"] != "human" or seat["ready"]
                    for seat in room["seats"]
                )
            )
            return state

        state.update(
            {
                "round": game["round"],
                "turn": game["turn"],
                "players": [
                    {
                        "name": seat["name"],
                        "type": seat["type"],
                        "ready": seat["ready"],
                        "is_host": seat["name"] == room["host"],
                        "penalty": game["penalties"][seat["name"]],
                        "round_penalty": game["round_penalties"].get(seat["name"], 0),
                        "wins": game["wins"].get(seat["name"], 0),
                        "hand_count": len(game["hands"][seat["name"]]),
                        "has_selected": seat["name"] in room["selected"],
                    }
                    for seat in room["seats"]
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
                "active_player": room["active_player"],
                "selected_count": len(room["selected"]),
                "selection_count": len(room["seats"]),
                "can_select_card": (
                    room["phase"] == "choose"
                    and self._seat(room, viewer)["type"] == "human"
                    and viewer not in room["selected"]
                ),
                "can_choose_row": (
                    room["phase"] == "row" and room["active_player"] == viewer
                ),
                "row_candidates": (
                    list(room["row_candidates"])
                    if room["phase"] == "row" and room["active_player"] == viewer
                    else []
                ),
                "played_cards": list(room["played_cards"]),
                "last_results": list(room["last_results"]),
                "winner": room["winner"],
                "winners": list(room["winners"]),
                "hand": (
                    [
                        {
                            "value": card["value"],
                            "color": card["color"],
                            "penalty": card["penalty"],
                        }
                        for card in game["hands"][viewer]
                    ]
                    if (
                        room["phase"] == "choose"
                        and self._seat(room, viewer)["type"] == "human"
                        and viewer not in room["selected"]
                    )
                    else None
                ),
            }
        )
        return state

    def _start_round(self, room: dict[str, Any], first_round: bool) -> None:
        if first_round:
            names = [seat["name"] for seat in room["seats"]]
            room["game"] = create_initial_game_state(names)
            room["game"]["player_types"] = {
                seat["name"]: seat["type"] for seat in room["seats"]
            }
        room["phase"] = "choose"
        room["selected"] = {}
        room["to_resolve"] = []
        room["active_player"] = None
        room["row_candidates"] = []
        room["winner"] = None
        room["winners"] = []
        room["played_cards"] = []
        room["last_results"] = []
        room["round"] = room["game"]["round"]
        room["turn"] = room["game"]["turn"]
        for seat in room["seats"]:
            if seat["type"] == "human":
                seat["ready"] = True
                continue
            hand = room["game"]["hands"][seat["name"]]
            card = (
                secrets.choice(hand)
                if seat["type"] == "random"
                else max(hand, key=lambda item: item["value"])
            )
            room["selected"][seat["name"]] = card["value"]
        if len(room["selected"]) == len(room["seats"]):
            self._begin_resolution_if_ready(room)

    def _begin_resolution_if_ready(self, room: dict[str, Any]) -> None:
        if len(room["selected"]) != len(room["seats"]):
            return
        game = room["game"]
        room["played_cards"] = [
            {
                "player": seat["name"],
                "card_value": room["selected"][seat["name"]],
                "color": next(
                    card["color"]
                    for card in game["hands"][seat["name"]]
                    if card["value"] == room["selected"][seat["name"]]
                ),
                "penalty": next(
                    card["penalty"]
                    for card in game["hands"][seat["name"]]
                    if card["value"] == room["selected"][seat["name"]]
                ),
            }
            for seat in room["seats"]
        ]
        room["played_cards"].sort(key=lambda play: play["card_value"])
        room["last_results"] = []
        room["phase"] = "resolve"
        room["to_resolve"] = sorted(
            room["selected"],
            key=lambda name: room["selected"][name],
        )
        self._continue_resolution(room)

    def _continue_resolution(self, room: dict[str, Any]) -> None:
        game = room["game"]
        while room["to_resolve"]:
            player = room["to_resolve"][0]
            value = room["selected"][player]
            lower_rows = [
                index
                for index, row in enumerate(game["rows"])
                if not row or max(card["value"] for card in row) < value
            ]
            if lower_rows:
                self._place_current(room, choose_best_row(game["rows"], value))
                continue

            candidates = [
                index
                for index, row in enumerate(game["rows"])
                if row and max(card["value"] for card in row) >= value
            ]
            seat = self._seat(room, player)
            if seat["type"] == "human":
                room["phase"] = "row"
                room["active_player"] = player
                room["row_candidates"] = candidates
                return
            if seat["type"] == "random":
                row_index = secrets.choice(candidates)
            else:
                row_index = min(
                    candidates,
                    key=lambda index: (
                        sum(card["penalty"] for card in game["rows"][index]),
                        index,
                    ),
                )
            self._place_current(room, row_index)

        game["round_over"] = any(not game["hands"][seat["name"]] for seat in room["seats"])
        if game["round_over"]:
            finish_round(game)
            room["winner"] = game["winner"]
            room["winners"] = list(game["winners"])
            room["phase"] = "round_over"
            room["active_player"] = None
            room["row_candidates"] = []
            return

        game["turn"] += 1
        room["turn"] = game["turn"]
        room["selected"] = {}
        room["to_resolve"] = []
        room["active_player"] = None
        room["row_candidates"] = []
        for seat in room["seats"]:
            if seat["type"] == "human":
                continue
            hand = game["hands"][seat["name"]]
            card = (
                secrets.choice(hand)
                if seat["type"] == "random"
                else max(hand, key=lambda item: item["value"])
            )
            room["selected"][seat["name"]] = card["value"]
        room["phase"] = "choose"
        self._begin_resolution_if_ready(room)

    def _place_current(self, room: dict[str, Any], row_index: int) -> None:
        game = room["game"]
        player = room["to_resolve"].pop(0)
        value = room["selected"][player]
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
        room["last_results"].append(
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
        room["active_player"] = None
        room["row_candidates"] = []

    def _seat(self, room: dict[str, Any], player: str) -> dict[str, Any]:
        return next(seat for seat in room["seats"] if seat["name"] == player)

    def _room_for_token(self, token: Any) -> tuple[dict[str, Any], str]:
        if not isinstance(token, str) or len(token) > 128:
            raise ValueError("参加トークンが無効です。再参加してください。")
        self._expire_rooms()
        identity = self.tokens.get(token)
        if identity is None:
            raise ValueError("参加トークンが無効です。部屋コードから再参加してください。")
        code, player = identity
        room = self.rooms.get(code)
        if room is None:
            self.tokens.pop(token, None)
            raise ValueError("部屋の有効期限が切れました。新しい部屋を作成してください。")
        return room, player

    def _new_room_code(self) -> str:
        for _ in range(32):
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(8))
            if code not in self.rooms:
                return code
        raise RuntimeError("部屋コードを発行できませんでした。")

    @staticmethod
    def _touch(room: dict[str, Any]) -> None:
        room["last_activity"] = time.monotonic()
        room["revision"] += 1

    def _new_token(self) -> str:
        return secrets.token_urlsafe(32)

    def _expire_rooms(self) -> None:
        now = time.monotonic()
        expired = [
            code
            for code, room in self.rooms.items()
            if now - room["last_activity"] > ROOM_TTL_SECONDS
        ]
        for code in expired:
            room = self.rooms.pop(code)
            for seat in room["seats"]:
                if seat["token"] is not None:
                    self.tokens.pop(seat["token"], None)

    @staticmethod
    def _validate_name(name: Any) -> str:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24:
            raise ValueError("表示名は1〜24文字で入力してください。")
        return name.strip()
