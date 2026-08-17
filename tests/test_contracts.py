import unittest
from datetime import date

from backend.config import add_months, flatten_contracts, get_contracts


class ContractRuleTests(unittest.TestCase):
    def test_add_months_crosses_year(self):
        self.assertEqual((2027, 1), add_months(2026, 12, 1))
        self.assertEqual((2027, 2), add_months(2026, 12, 2))

    def test_may_contracts(self):
        self.assertEqual(
            ["SHFE.cu2606", "SHFE.cu2607", "INE.bc2606", "INE.bc2607"],
            flatten_contracts(get_contracts(date(2026, 5, 7))),
        )

    def test_june_contracts(self):
        self.assertEqual(
            ["SHFE.cu2607", "SHFE.cu2608", "INE.bc2607", "INE.bc2608"],
            flatten_contracts(get_contracts(date(2026, 6, 15))),
        )

    def test_december_contracts_roll_year(self):
        self.assertEqual(
            ["SHFE.cu2701", "SHFE.cu2702", "INE.bc2701", "INE.bc2702"],
            flatten_contracts(get_contracts(date(2026, 12, 20))),
        )


if __name__ == "__main__":
    unittest.main()
