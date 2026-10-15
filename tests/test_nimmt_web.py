import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nimmt_web
from nimmt import MAX_LOG_ENTRIES, append_log
from nimmt_web import NimmtRequestHandler, NimmtWebApp
from nimmt_online import OnlineRoomManager, ROOM_TTL_SECONDS


def make_card(value, color="red", penalty=1):
    return {
        "value": value,
        "color": color,
        "id": f"{color[0]}-{value}",
        "penalty": penalty,
    }


class NimmtWebAppTest(unittest.TestCase):
    def setUp(self):
        self.app = NimmtWebApp()
        self.state = self.app.start(
            {
                "players": ["Alice", "Bob"],
                "player_types": {"Alice": "human", "Bob": "human"},
            }
        )

    def test_handoff_hides_every_hand_until_active_player_acknowledges(self):
        self.assertTrue(self.state["awaiting_handoff"])
        self.assertIsNone(self.state["hand"])

        self.state = self.app.acknowledge_handoff()
        self.assertEqual(self.state["active_player"], "Alice")
        self.assertEqual(len(self.state["hand"]), 10)

        card_value = self.state["hand"][0]["value"]
        self.state = self.app.submit_card({"card_value": card_value})

        self.assertEqual(self.state["active_player"], "Bob")
        self.assertTrue(self.state["awaiting_handoff"])
        self.assertIsNone(self.state["hand"])
        self.assertNotIn("pending", self.state)
        self.assertNotIn("selected_cards", self.state)

    def test_row_selection_is_requested_only_when_no_lower_row_exists(self):
        game = self.app.game
        game["rows"] = [
            [make_card(20, penalty=3)],
            [make_card(30, penalty=5)],
            [make_card(40, penalty=1)],
            [make_card(50, penalty=1)],
        ]
        game["hands"]["Alice"] = [make_card(10)]
        game["hands"]["Bob"] = [make_card(11)]

        self.app.acknowledge_handoff()
        self.state = self.app.submit_card({"card_value": 10})
        self.app.acknowledge_handoff()
        self.state = self.app.submit_card({"card_value": 11})

        self.assertEqual(self.state["phase"], "row")
        self.assertEqual(self.state["active_player"], "Alice")
        self.assertEqual(self.state["resolution_card"]["value"], 10)
        self.assertEqual(self.state["row_candidates"], [0, 1, 2, 3])
        self.assertIsNone(self.state["hand"])

        self.state = self.app.acknowledge_handoff()
        self.assertEqual(self.state["phase"], "row")
        self.assertEqual(self.state["rows"][0]["penalty"], 3)
        self.state = self.app.choose_row({"row_index": 0})

        self.assertEqual(self.state["phase"], "round_over")
        self.assertEqual(self.state["players"][0]["penalty"], 3)
        self.assertEqual(self.state["players"][1]["penalty"], 0)
        self.assertEqual(self.state["winner"], "Bob")

    def test_played_cards_are_revealed_together_and_sixth_card_collection_is_explained(self):
        self.app.game["rows"] = [
            [make_card(value, penalty=1) for value in range(1, 6)],
            [make_card(30)],
            [make_card(40)],
            [make_card(50)],
        ]
        self.app.game["hands"]["Alice"] = [make_card(10, "yellow", 2)]
        self.app.game["hands"]["Bob"] = [make_card(15, "green", 1)]

        self.app.acknowledge_handoff()
        state = self.app.submit_card({"card_value": 10})
        self.assertEqual(state["played_cards"], [])

        self.app.acknowledge_handoff()
        state = self.app.submit_card({"card_value": 15})

        self.assertEqual(
            [(play["player"], play["card_value"]) for play in state["played_cards"]],
            [("Alice", 10), ("Bob", 15)],
        )
        collection = next(
            result
            for result in state["last_results"]
            if result["collection_reason"] == "sixth_card"
        )
        self.assertEqual(collection["row"], 0)
        self.assertEqual(collection["penalty"], 5)
        self.assertEqual([card["value"] for card in collection["collected_cards"]], [1, 2, 3, 4, 5])

    def test_invalid_card_and_row_choices_are_rejected(self):
        self.app.acknowledge_handoff()
        with self.assertRaisesRegex(ValueError, "手札に"):
            self.app.submit_card({"card_value": 105})
        self.assertEqual(len(self.app.pending), 0)

        with self.assertRaisesRegex(ValueError, "先にゲーム"):
            NimmtWebApp().submit_card({"card_value": 10})

    def test_player_setup_rejects_duplicate_names(self):
        with self.assertRaisesRegex(ValueError, "重複"):
            self.app.start(
                {
                    "players": ["Alice", "Alice"],
                    "player_types": {"Alice": "human"},
                }
            )

    def test_player_setup_rejects_invalid_types_and_long_names(self):
        with self.assertRaisesRegex(ValueError, "タイプ"):
            self.app.start(
                {
                    "players": ["Alice", "Bob"],
                    "player_types": {"Alice": [], "Bob": "human"},
                }
            )
        with self.assertRaisesRegex(ValueError, "24文字"):
            self.app.start(
                {
                    "players": ["A" * 25, "Bob"],
                    "player_types": {"A" * 25: "human", "Bob": "human"},
                }
            )

    def test_ai_player_plays_between_human_selections(self):
        app = NimmtWebApp()
        state = app.start(
            {
                "players": ["Alice", "CPU", "Carol"],
                "player_types": {"Alice": "human", "CPU": "ai", "Carol": "human"},
            }
        )
        app.game["rows"] = [[make_card(value)] for value in (10, 20, 30, 40)]
        app.game["hands"] = {
            "Alice": [make_card(50), make_card(70)],
            "CPU": [make_card(101), make_card(102)],
            "Carol": [make_card(60), make_card(80)],
        }
        state = app.acknowledge_handoff()
        state = app.submit_card({"card_value": 50})

        self.assertEqual(state["active_player"], "Carol")
        self.assertIsNone(state["hand"])
        self.assertEqual(len(app.game["hands"]["CPU"]), 2)

        state = app.acknowledge_handoff()
        state = app.submit_card({"card_value": 60})

        self.assertEqual(state["turn"], 2)
        self.assertEqual(len(app.game["hands"]["CPU"]), 1)
        self.assertEqual(len(app.game["hands"]["Alice"]), 1)
        self.assertEqual(len(app.game["hands"]["Carol"]), 1)

    def test_next_round_keeps_cumulative_penalties(self):
        self.app.game["penalties"] = {"Alice": 8, "Bob": 3}
        self.app.phase = "round_over"

        state = self.app.next_round()

        self.assertEqual(state["round"], 2)
        self.assertEqual({player["name"]: player["penalty"] for player in state["players"]}, {
            "Alice": 8,
            "Bob": 3,
        })
        self.assertEqual(state["phase"], "choose")

    def play_last_cards(self, alice_value, bob_value):
        self.app.acknowledge_handoff()
        self.app.submit_card({"card_value": alice_value})
        self.app.acknowledge_handoff()
        return self.app.submit_card({"card_value": bob_value})

    def test_round_winner_is_lowest_round_penalty_not_lowest_total(self):
        game = self.app.game
        game["penalties"] = {"Alice": 0, "Bob": 20}
        game["rows"] = [
            [make_card(value) for value in range(1, 6)],
            [make_card(30)],
            [make_card(40)],
            [make_card(50)],
        ]
        game["hands"]["Alice"] = [make_card(10)]
        game["hands"]["Bob"] = [make_card(31)]

        state = self.play_last_cards(10, 31)

        players = {player["name"]: player for player in state["players"]}
        self.assertEqual(state["phase"], "round_over")
        self.assertEqual(players["Alice"]["round_penalty"], 5)
        self.assertEqual(players["Bob"]["round_penalty"], 0)
        self.assertEqual(players["Alice"]["penalty"], 5)
        self.assertEqual(players["Bob"]["penalty"], 20)
        self.assertEqual(state["winners"], ["Bob"])
        self.assertEqual(state["winner"], "Bob")
        self.assertEqual(players["Alice"]["wins"], 0)
        self.assertEqual(players["Bob"]["wins"], 1)

    def test_tied_round_penalty_makes_every_tied_player_a_winner(self):
        game = self.app.game
        game["penalties"] = {"Alice": 7, "Bob": 2}
        game["rows"] = [[make_card(5)], [make_card(30)], [make_card(40)], [make_card(50)]]
        game["hands"]["Alice"] = [make_card(10)]
        game["hands"]["Bob"] = [make_card(31)]

        state = self.play_last_cards(10, 31)

        self.assertEqual(state["winners"], ["Alice", "Bob"])
        self.assertEqual(
            {player["name"]: player["wins"] for player in state["players"]},
            {"Alice": 1, "Bob": 1},
        )

    def test_next_round_keeps_cumulative_wins_and_clears_round_winners(self):
        self.app.game["wins"] = {"Alice": 2, "Bob": 1}
        self.app.game["winners"] = ["Alice"]
        self.app.phase = "round_over"

        state = self.app.next_round()

        self.assertEqual(
            {player["name"]: player["wins"] for player in state["players"]},
            {"Alice": 2, "Bob": 1},
        )
        self.assertEqual(state["winners"], [])

    def test_game_log_starts_with_round_start(self):
        self.assertEqual(
            [(entry["kind"], entry["message"]) for entry in self.state["log"]],
            [("round_start", "ラウンド1開始")],
        )

    def test_game_log_records_placements_collections_and_round_end(self):
        game = self.app.game
        game["rows"] = [
            [make_card(value) for value in range(1, 6)],
            [make_card(30)],
            [make_card(40)],
            [make_card(50)],
        ]
        game["hands"]["Alice"] = [make_card(10)]
        game["hands"]["Bob"] = [make_card(31)]

        state = self.play_last_cards(10, 31)

        log = state["log"]
        self.assertEqual(
            [entry["kind"] for entry in log],
            ["round_start", "place", "place", "round_end"],
        )
        sixth, normal, round_end = log[1], log[2], log[3]
        self.assertEqual(sixth["reason"], "sixth_card")
        self.assertEqual(sixth["penalty"], 5)
        self.assertEqual((sixth["round"], sixth["turn"]), (1, 1))
        self.assertEqual(
            sixth["message"],
            "Aliceが出したカード10が6枚目になったため、列1を回収し、ペナルティ5点を食らった。",
        )
        self.assertEqual(normal["message"], "Bobがカード31を列2に置いた。")
        self.assertEqual(normal["penalty"], 0)
        self.assertEqual(round_end["winners"], ["Bob"])
        self.assertEqual(
            round_end["message"],
            "ラウンド1終了：勝者 Bob（このラウンドのペナルティ0点）",
        )

    def test_game_log_explains_row_choice_collection(self):
        game = self.app.game
        game["rows"] = [
            [make_card(20, penalty=3)],
            [make_card(30)],
            [make_card(40)],
            [make_card(50)],
        ]
        game["hands"]["Alice"] = [make_card(10), make_card(60)]
        game["hands"]["Bob"] = [make_card(11), make_card(61)]

        self.play_last_cards(10, 11)
        self.app.acknowledge_handoff()
        state = self.app.choose_row({"row_index": 0})

        choice = next(entry for entry in state["log"] if entry.get("reason") == "player_choice")
        self.assertEqual(
            choice["message"],
            "Aliceが出したカード10はどの列の末尾よりも小さいため、列1を回収し、ペナルティ3点を食らった。",
        )
        self.assertEqual(state["phase"], "choose")

    def test_game_log_lists_tied_winners_and_survives_next_round_until_reset(self):
        game = self.app.game
        game["rows"] = [[make_card(5)], [make_card(30)], [make_card(40)], [make_card(50)]]
        game["hands"]["Alice"] = [make_card(10)]
        game["hands"]["Bob"] = [make_card(31)]
        state = self.play_last_cards(10, 31)
        self.assertEqual(
            state["log"][-1]["message"],
            "ラウンド1終了：勝者 Alice、Bob（このラウンドのペナルティ0点）",
        )

        state = self.app.next_round()
        self.assertEqual(len(state["log"]), 5)
        self.assertEqual(state["log"][-1]["message"], "ラウンド2開始")

        self.app.reset()
        self.assertEqual(self.app.log, [])

    def test_game_log_keeps_only_the_newest_entries(self):
        log = []
        for index in range(MAX_LOG_ENTRIES + 5):
            append_log(log, {"kind": "place", "message": str(index)})

        self.assertEqual(len(log), MAX_LOG_ENTRIES)
        self.assertEqual(log[0]["message"], "5")
        self.assertEqual(log[-1]["message"], str(MAX_LOG_ENTRIES + 4))

    def test_reset_clears_the_game_and_private_hand(self):
        self.app.acknowledge_handoff()

        state = self.app.reset()

        self.assertEqual(state, {"phase": "setup"})
        self.assertIsNone(self.app.game)
        self.assertIsNone(self.app.active_player)


class OnlineRoomManagerTest(unittest.TestCase):
    def setUp(self):
        self.manager = OnlineRoomManager()
        self.host = self.manager.create_room("Host", 4)
        self.guest = self.manager.join_room(self.host["room_code"], "Guest")

    def test_room_requires_ready_players_and_host_starts(self):
        with self.assertRaisesRegex(ValueError, "全員が準備完了"):
            self.manager.start_game(self.host["token"])
        with self.assertRaisesRegex(ValueError, "部屋主"):
            self.manager.start_game(self.guest["token"])

        ready = self.manager.set_ready(self.guest["token"])
        self.assertTrue(ready["players"][1]["ready"])
        state = self.manager.start_game(self.host["token"])

        self.assertEqual(state["phase"], "choose")
        self.assertEqual(len(state["hand"]), 10)
        self.assertEqual(state["selection_count"], 2)

    def test_only_seat_owner_receives_hand_and_other_cards_remain_private(self):
        self.manager.set_ready(self.guest["token"])
        host_state = self.manager.start_game(self.host["token"])
        guest_state = self.manager.state(self.guest["token"])

        self.assertEqual(len(host_state["hand"]), 10)
        self.assertEqual(len(guest_state["hand"]), 10)
        self.assertNotIn("token", guest_state)

        host_card = host_state["hand"][0]["value"]
        host_state = self.manager.choose_card(self.host["token"], host_card)
        guest_state = self.manager.state(self.guest["token"])
        self.assertIsNone(host_state["hand"])
        self.assertTrue(guest_state["can_select_card"])
        self.assertEqual(guest_state["selected_count"], 1)
        self.assertNotIn("selected_cards", guest_state)

    def test_online_cards_reveal_only_after_everyone_chooses_and_report_sixth_card_collection(self):
        self.manager.set_ready(self.guest["token"])
        self.manager.start_game(self.host["token"])
        room = self.manager.rooms[self.host["room_code"]]
        room["game"]["rows"] = [
            [make_card(value, penalty=1) for value in range(1, 6)],
            [make_card(30)],
            [make_card(40)],
            [make_card(50)],
        ]
        room["game"]["hands"]["Host"] = [make_card(10, "yellow", 2)]
        room["game"]["hands"]["Guest"] = [make_card(15, "green", 1)]

        state = self.manager.choose_card(self.host["token"], 10)
        self.assertEqual(state["played_cards"], [])
        state = self.manager.choose_card(self.guest["token"], 15)

        self.assertEqual(
            [(play["player"], play["card_value"]) for play in state["played_cards"]],
            [("Host", 10), ("Guest", 15)],
        )
        collection = next(
            result
            for result in state["last_results"]
            if result["collection_reason"] == "sixth_card"
        )
        self.assertEqual(collection["penalty"], 5)
        self.assertEqual([card["value"] for card in collection["collected_cards"]], [1, 2, 3, 4, 5])

    def test_online_round_winner_uses_round_penalty_and_counts_wins(self):
        self.manager.set_ready(self.guest["token"])
        state = self.manager.start_game(self.host["token"])
        self.assertEqual(state["winners"], [])
        self.assertEqual([player["wins"] for player in state["players"]], [0, 0])

        room = self.manager.rooms[self.host["room_code"]]
        room["game"]["penalties"] = {"Host": 0, "Guest": 20}
        room["game"]["rows"] = [
            [make_card(value) for value in range(1, 6)],
            [make_card(30)],
            [make_card(40)],
            [make_card(50)],
        ]
        room["game"]["hands"]["Host"] = [make_card(10)]
        room["game"]["hands"]["Guest"] = [make_card(31)]

        self.manager.choose_card(self.host["token"], 10)
        state = self.manager.choose_card(self.guest["token"], 31)

        self.assertEqual(state["phase"], "round_over")
        self.assertEqual(state["winners"], ["Guest"])
        self.assertEqual(state["winner"], "Guest")
        self.assertEqual(
            {player["name"]: player["wins"] for player in state["players"]},
            {"Host": 0, "Guest": 1},
        )

        self.assertEqual(
            [entry["kind"] for entry in state["log"]],
            ["round_start", "place", "place", "round_end"],
        )
        self.assertEqual(
            state["log"][1]["message"],
            "Hostが出したカード10が6枚目になったため、列1を回収し、ペナルティ5点を食らった。",
        )
        self.assertEqual(state["log"][-1]["winners"], ["Guest"])

        state = self.manager.next_round(self.host["token"])
        self.assertEqual(state["log"][-1]["message"], "ラウンド2開始")
        self.assertEqual(len(state["log"]), 5)
        self.assertEqual(state["winners"], [])
        self.assertEqual(
            {player["name"]: player["wins"] for player in state["players"]},
            {"Host": 0, "Guest": 1},
        )

    def test_seat_token_cannot_play_a_card_from_another_seat(self):
        self.manager.set_ready(self.guest["token"])
        self.manager.start_game(self.host["token"])
        guest_hand = self.manager.state(self.guest["token"])["hand"]
        with self.assertRaisesRegex(ValueError, "手札"):
            self.manager.choose_card(self.host["token"], guest_hand[0]["value"])

    def test_only_active_player_can_choose_a_penalty_row(self):
        self.manager.set_ready(self.guest["token"])
        self.manager.start_game(self.host["token"])
        room = self.manager.rooms[self.host["room_code"]]
        room["game"]["rows"] = [
            [make_card(20, penalty=3)],
            [make_card(30, penalty=1)],
            [make_card(40, penalty=1)],
            [make_card(50, penalty=1)],
        ]
        room["game"]["hands"]["Host"] = [make_card(10), make_card(60)]
        room["game"]["hands"]["Guest"] = [make_card(11), make_card(61)]

        self.manager.choose_card(self.host["token"], 10)
        self.manager.choose_card(self.guest["token"], 11)
        guest_state = self.manager.state(self.guest["token"])
        self.assertEqual(guest_state["phase"], "row")
        self.assertFalse(guest_state["can_choose_row"])
        self.assertEqual(guest_state["row_candidates"], [])

        with self.assertRaisesRegex(ValueError, "このプレイヤー"):
            self.manager.choose_row(self.guest["token"], 0)
        host_state = self.manager.choose_row(self.host["token"], 0)

        self.assertEqual(host_state["turn"], 2)
        self.assertEqual(host_state["players"][0]["penalty"], 3)

    def test_bot_seat_is_host_controlled_and_plays_automatically(self):
        self.manager.add_bot(self.host["token"], "ai")
        self.manager.set_ready(self.guest["token"])
        state = self.manager.start_game(self.host["token"])

        self.assertEqual(state["selection_count"], 3)
        self.assertEqual(state["selected_count"], 1)
        self.assertEqual([player["type"] for player in state["players"]], [
            "human",
            "human",
            "ai",
        ])

    def test_lobby_join_is_rejected_after_game_starts(self):
        self.manager.set_ready(self.guest["token"])
        self.manager.start_game(self.host["token"])

        with self.assertRaisesRegex(ValueError, "見つかりません"):
            self.manager.join_room(self.host["room_code"], "Late Player")

    def test_rooms_are_isolated_and_expire_after_inactivity(self):
        other_room = self.manager.create_room("Other Host", 2)
        self.assertNotEqual(self.host["room_code"], other_room["room_code"])
        self.assertEqual(self.manager.state(self.host["token"])["players"][0]["name"], "Host")
        self.assertEqual(self.manager.state(other_room["token"])["players"][0]["name"], "Other Host")

        room = self.manager.rooms[self.host["room_code"]]
        room["last_activity"] -= ROOM_TTL_SECONDS + 1
        with self.assertRaisesRegex(ValueError, "参加トークン"):
            self.manager.state(self.host["token"])
        self.assertNotIn(self.host["room_code"], self.manager.rooms)


class StaticFileTest(unittest.TestCase):
    def setUp(self):
        pic_dir = tempfile.TemporaryDirectory()
        self.addCleanup(pic_dir.cleanup)
        self.pic_root = Path(pic_dir.name)
        for patcher in (
            mock.patch.object(nimmt_web, "PIC_ROOT", self.pic_root),
            mock.patch.object(NimmtRequestHandler, "log_message"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), NimmtRequestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def test_explosion_art_is_served_as_webp(self):
        (self.pic_root / "explosion.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
        with urlopen(f"{self.base_url}/pic/explosion.webp") as response:
            self.assertEqual(response.headers["Content-Type"], "image/webp")
            self.assertEqual(response.read(), b"RIFF\x00\x00\x00\x00WEBP")

    def test_missing_explosion_art_returns_404(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base_url}/pic/explosion.webp")
        caught.exception.close()
        self.assertEqual(caught.exception.code, 404)

    def test_only_listed_files_are_served_from_pic(self):
        (self.pic_root / "other.webp").write_bytes(b"secret")
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base_url}/pic/other.webp")
        caught.exception.close()
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
