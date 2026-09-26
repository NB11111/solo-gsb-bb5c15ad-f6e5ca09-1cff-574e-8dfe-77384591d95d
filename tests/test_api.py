"""HTTP-level tests for the FastAPI app (require fastapi + httpx installed)."""

import unittest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PAYLOAD = {
    "segments": [100, 251, 80],
    "passengers": [
        {"id": "alice", "board": 0, "alight": 3},
        {"id": "bob", "board": 1, "alight": 3},
        {"id": "carol", "board": 0, "alight": 1},
    ],
}

SETTLE_PAYLOAD = dict(PAYLOAD, payments=[
    {"id": "alice", "amount": 331},
    {"id": "bob", "amount": 100},
    {"id": "carol", "amount": 0},
])


class SplitApiTests(unittest.TestCase):
    def test_split_ok(self):
        resp = client.post("/split", json=PAYLOAD)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_cost"], 431)
        self.assertEqual(body["totals"], {"alice": 216, "bob": 165, "carol": 50})
        self.assertEqual(sum(body["totals"].values()), body["total_cost"])
        self.assertEqual(len(body["segments"]), 3)
        for seg, cost in zip(body["segments"], PAYLOAD["segments"]):
            self.assertEqual(seg["cost"], cost)
            self.assertEqual(sum(seg["shares"].values()), cost)
        self.assertEqual(body["segments"][0]["passengers"], ["alice", "carol"])
        self.assertEqual(body["segments"][1]["shares"], {"alice": 126, "bob": 125})

    def test_passenger_order_does_not_matter(self):
        reversed_payload = dict(PAYLOAD, passengers=list(reversed(PAYLOAD["passengers"])))
        r1 = client.post("/split", json=PAYLOAD)
        r2 = client.post("/split", json=reversed_payload)
        self.assertEqual(r1.status_code, r2.status_code)
        self.assertEqual(r1.json(), r2.json())

    def test_empty_load_charged_segment_is_422(self):
        payload = {"segments": [100, 0, 50],
                   "passengers": [{"id": "a", "board": 2, "alight": 3}]}
        resp = client.post("/split", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("no passengers on board", resp.text)

    def test_zero_cost_empty_segment_is_ok(self):
        payload = {"segments": [0, 50],
                   "passengers": [{"id": "a", "board": 1, "alight": 2}]}
        resp = client.post("/split", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["totals"], {"a": 50})

    def test_board_not_before_alight_is_422(self):
        payload = {"segments": [10, 10],
                   "passengers": [{"id": "a", "board": 1, "alight": 1}]}
        resp = client.post("/split", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("invalid interval", resp.text)

    def test_station_out_of_range_is_422(self):
        payload = {"segments": [10, 10],
                   "passengers": [{"id": "a", "board": 0, "alight": 3}]}
        resp = client.post("/split", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("out of range", resp.text)

    def test_negative_board_is_422(self):
        payload = {"segments": [10],
                   "passengers": [{"id": "a", "board": -1, "alight": 1}]}
        self.assertEqual(client.post("/split", json=payload).status_code, 422)

    def test_duplicate_passenger_id_is_422(self):
        payload = {"segments": [10],
                   "passengers": [{"id": "a", "board": 0, "alight": 1},
                                  {"id": "a", "board": 0, "alight": 1}]}
        resp = client.post("/split", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("duplicate passenger id", resp.text)

    def test_negative_cost_is_422(self):
        payload = {"segments": [10, -5],
                   "passengers": [{"id": "a", "board": 0, "alight": 2}]}
        self.assertEqual(client.post("/split", json=payload).status_code, 422)

    def test_empty_segments_is_422(self):
        self.assertEqual(client.post("/split", json={"segments": []}).status_code, 422)

    def test_unknown_field_is_422(self):
        payload = dict(PAYLOAD, currency="usd")
        self.assertEqual(client.post("/split", json=payload).status_code, 422)


class SettleApiTests(unittest.TestCase):
    def test_settle_ok(self):
        resp = client.post("/settle", json=SETTLE_PAYLOAD)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["owed"], {"alice": 216, "bob": 165, "carol": 50})
        self.assertEqual(body["paid"], {"alice": 331, "bob": 100, "carol": 0})
        self.assertEqual(body["balance"], {"alice": 115, "bob": -65, "carol": -50})
        self.assertEqual(body["transfers"], [
            {"from": "bob", "to": "alice", "amount": 65},
            {"from": "carol", "to": "alice", "amount": 50},
        ])
        # balances are exactly paid - owed and sum to zero
        for pid in body["owed"]:
            self.assertEqual(body["balance"][pid], body["paid"][pid] - body["owed"][pid])
        self.assertEqual(sum(body["balance"].values()), 0)
        # every transfer moves money from a debtor to a creditor
        for tr in body["transfers"]:
            self.assertLess(body["balance"][tr["from"]], 0)
            self.assertGreater(body["balance"][tr["to"]], 0)
            self.assertGreater(tr["amount"], 0)

    def test_zero_balances_mean_no_transfers(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1},
                           {"id": "b", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": 50}, {"id": "b", "amount": 50}],
        }
        body = client.post("/settle", json=payload).json()
        self.assertEqual(body["balance"], {"a": 0, "b": 0})
        self.assertEqual(body["transfers"], [])

    def test_empty_trip(self):
        payload = {"segments": [0], "passengers": [], "payments": []}
        resp = client.post("/settle", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(),
                         {"owed": {}, "paid": {}, "balance": {}, "transfers": []})

    def test_input_order_does_not_matter(self):
        import random
        baseline = client.post("/settle", json=SETTLE_PAYLOAD).json()
        for seed in range(5):
            shuffled = {
                "segments": SETTLE_PAYLOAD["segments"],
                "passengers": SETTLE_PAYLOAD["passengers"][:],
                "payments": SETTLE_PAYLOAD["payments"][:],
            }
            random.Random(seed).shuffle(shuffled["passengers"])
            random.Random(seed + 100).shuffle(shuffled["payments"])
            resp = client.post("/settle", json=shuffled)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json(), baseline)

    def test_integer_passenger_ids(self):
        payload = {
            "segments": [10, 10],
            "passengers": [{"id": 1, "board": 0, "alight": 2},
                           {"id": 2, "board": 0, "alight": 2}],
            "payments": [{"id": 2, "amount": 20}, {"id": 1, "amount": 0}],
        }
        body = client.post("/settle", json=payload).json()
        self.assertEqual(body["owed"], {"1": 10, "2": 10})
        self.assertEqual(body["paid"], {"1": 0, "2": 20})
        self.assertEqual(body["balance"], {"1": -10, "2": 10})
        self.assertEqual(body["transfers"], [{"from": 1, "to": 2, "amount": 10}])

    def test_payments_total_mismatch_is_422(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": 99}],
        }
        resp = client.post("/settle", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("payments total", resp.text)

    def test_missing_payment_is_422(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1},
                           {"id": "b", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": 100}],
        }
        resp = client.post("/settle", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("missing payment", resp.text)

    def test_duplicate_payment_id_is_422(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": 50}, {"id": "a", "amount": 50}],
        }
        resp = client.post("/settle", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("duplicate payment id", resp.text)

    def test_unknown_payment_id_is_422(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": 99}, {"id": "ghost", "amount": 1}],
        }
        resp = client.post("/settle", json=payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("unknown passenger", resp.text)

    def test_negative_payment_is_422(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": -1}],
        }
        self.assertEqual(client.post("/settle", json=payload).status_code, 422)

    def test_non_integer_payment_is_422(self):
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 1}],
            "payments": [{"id": "a", "amount": 50.5}],
        }
        self.assertEqual(client.post("/settle", json=payload).status_code, 422)

    def test_missing_payments_field_is_422(self):
        payload = {"segments": [100],
                   "passengers": [{"id": "a", "board": 0, "alight": 1}]}
        self.assertEqual(client.post("/settle", json=payload).status_code, 422)

    def test_split_trip_rules_still_apply_on_settle(self):
        # the trip itself is invalid (empty charged segment) even though
        # payments total matches
        payload = {
            "segments": [100],
            "passengers": [{"id": "a", "board": 0, "alight": 0}],
            "payments": [{"id": "a", "amount": 100}],
        }
        self.assertEqual(client.post("/settle", json=payload).status_code, 422)

    def test_healthz(self):
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
