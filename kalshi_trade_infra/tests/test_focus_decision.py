"""Todd's 2026-10-31 card ("Yes, weather + sports, $240/day cap") widens his stored focus once."""
import json

from app import schema


class _Cur:
    def __init__(self, focus, chat=()):
        self.focus, self.chat, self.row, self.lastrowid = focus, list(chat), None, 1

    def execute(self, sql, args=()):
        if sql.startswith("SELECT autopilot_focus"):
            self.row = {"autopilot_focus": self.focus}
        elif sql.startswith("UPDATE app_users SET autopilot_focus"):
            self.focus = args[0]
        elif sql.startswith("SELECT 1 FROM app_chat_messages"):
            needle = args[2].strip("%")
            self.row = {"1": 1} if any(needle in c for c in self.chat) else None
        elif sql.startswith("INSERT INTO app_chat_messages"):
            self.chat.append(args[2])

    def fetchone(self):
        return self.row


def test_the_decision_adds_weather_and_sports_once_and_says_so_in_chat():
    cur = _Cur(json.dumps(["crypto"]))
    assert schema.apply_focus_decision(cur)
    assert json.loads(cur.focus) == ["crypto", "weather-high", "sports"]
    assert cur.chat[0].startswith("Market focus set to crypto, daily high temperatures, sports.")
    cur.focus = json.dumps(["crypto"])                       # he narrows it again later: never overwritten
    assert not schema.apply_focus_decision(cur) and json.loads(cur.focus) == ["crypto"]


def test_no_focus_means_everything_is_already_in_scope():
    cur = _Cur(None)
    assert not schema.apply_focus_decision(cur) and cur.focus is None and cur.chat == []
