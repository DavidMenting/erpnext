from unittest import mock
from unittest.mock import patch

import frappe
from frappe.utils import flt

from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.tests.utils import ERPNextTestSuite


def resolve_on_gross(calc, item, tax):
	# base = gross printed line amount
	return flt(item.amount)


def resolve_on_mrp(calc, item, tax):
	# base = MRP, not net
	return flt(item.price_list_rate) * flt(item.qty)


class TestTaxesAndTotals(ERPNextTestSuite):
	def test_regional_round_off_accounts(self):
		"""
		Regional overrides cannot extend the list in-place — the return
		value must be assigned back to frappe.flags.round_off_applicable_accounts.
		"""
		test_account = "_Test Round Off Account"

		def mock_regional(company, account_list: list, doc=None) -> list:
			# Simulates a regional override
			account_list.extend([test_account])
			return account_list

		so = make_sales_order(do_not_save=True)

		with patch(
			"erpnext.controllers.taxes_and_totals.get_regional_round_off_accounts",
			mock_regional,
		):
			calculate_taxes_and_totals(so)

		self.assertIn(test_account, frappe.flags.round_off_applicable_accounts)

	def test_exclusive_custom_charge_on_resolved_base(self):
		"""Added (exclusive) custom charge_type whose base is resolved by the
		`erpnext_taxable_base_resolvers` hook. IPI 10% on the gross product value 1000
		-> tax 100, net 1000, grand 1100."""
		so = make_sales_order(do_not_save=True)
		so.items = []
		so.append(
			"items",
			{
				"item_code": "_Test Item",
				"qty": 1,
				"rate": 1000,
				"price_list_rate": 1000,
				"warehouse": "_Test Warehouse - _TC",
			},
		)
		so.set("taxes", [])
		so.append(
			"taxes",
			{
				"charge_type": "On Gross Value",
				"account_head": "_Test Account Excise Duty - _TC",
				"description": "IPI 10% on gross product value",
				"rate": 10,
				"cost_center": "_Test Cost Center - _TC",
			},
		)

		real_get_hooks = frappe.get_hooks

		def fake_get_hooks(hook=None, *args, **kwargs):
			if hook == "erpnext_taxable_base_resolvers":
				return {
					"On Gross Value": ["erpnext.controllers.tests.test_taxes_and_totals.resolve_on_gross"]
				}
			return real_get_hooks(hook, *args, **kwargs)

		with mock.patch("frappe.get_hooks", side_effect=fake_get_hooks):
			calculate_taxes_and_totals(so)

		self.assertEqual(so.net_total, 1000.0)
		self.assertEqual(so.taxes[0].tax_amount, 100.0)
		self.assertEqual(so.grand_total, 1100.0)

	def test_inclusive_custom_charge_on_resolved_base(self):
		"""Inclusive custom charge on a resolved base backs out non-compounding
		(tax = rate x resolved base) — a resolved base is fixed, so it never
		compounds. MRP 1200, printed 1000, rate 10%: tax 120, net 880."""
		so = make_sales_order(do_not_save=True)
		so.items = []
		so.append(
			"items",
			{
				"item_code": "_Test Item",
				"qty": 1,
				"rate": 1000,
				"price_list_rate": 1200,
				"warehouse": "_Test Warehouse - _TC",
			},
		)
		so.set("taxes", [])
		so.append(
			"taxes",
			{
				"charge_type": "On MRP",
				"account_head": "_Test Account VAT - _TC",
				"description": "Tax 10% on MRP, inclusive",
				"rate": 10,
				"included_in_print_rate": 1,
				"cost_center": "_Test Cost Center - _TC",
			},
		)

		real_get_hooks = frappe.get_hooks

		def fake_get_hooks(hook=None, *args, **kwargs):
			if hook == "erpnext_taxable_base_resolvers":
				return {"On MRP": ["erpnext.controllers.tests.test_taxes_and_totals.resolve_on_mrp"]}
			return real_get_hooks(hook, *args, **kwargs)

		with mock.patch("frappe.get_hooks", side_effect=fake_get_hooks):
			calculate_taxes_and_totals(so)

		self.assertEqual(so.taxes[0].tax_amount, 120.0)
		self.assertEqual(so.net_total, 880.0)
		self.assertEqual(so.grand_total, 1000.0)

	def test_disabling_rounded_total_resets_base_fields(self):
		"""Disabling rounded total should also clear base rounded values."""
		so = make_sales_order(do_not_save=True)
		so.items[0].qty = 1
		so.items[0].rate = 1000.25
		so.items[0].price_list_rate = 1000.25
		so.items[0].discount_percentage = 0
		so.items[0].discount_amount = 0
		so.set("taxes", [])

		so.disable_rounded_total = 0
		calculate_taxes_and_totals(so)

		self.assertEqual(so.grand_total, 1000.25)
		self.assertEqual(so.rounded_total, 1000.0)
		self.assertEqual(so.rounding_adjustment, -0.25)
		self.assertEqual(so.base_grand_total, 1000.25)
		self.assertEqual(so.base_rounded_total, 1000.0)
		self.assertEqual(so.base_rounding_adjustment, -0.25)

		# User toggles disable_rounded_total after values are already set.
		so.disable_rounded_total = 1

		calculate_taxes_and_totals(so)

		self.assertEqual(so.rounded_total, 0)
		self.assertEqual(so.rounding_adjustment, 0)
		self.assertEqual(so.base_rounded_total, 0)
		self.assertEqual(so.base_rounding_adjustment, 0)

	def make_inclusive_tax_order(self, lines, rate=21):
		so = make_sales_order(do_not_save=True)
		so.items = []
		for qty, item_rate in lines:
			so.append(
				"items",
				{
					"item_code": "_Test Item",
					"qty": qty,
					"rate": item_rate,
					"price_list_rate": item_rate,
					"warehouse": "_Test Warehouse - _TC",
				},
			)

		so.set("taxes", [])
		so.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": "_Test Account VAT - _TC",
				"description": f"VAT {rate}% inclusive",
				"rate": rate,
				"included_in_print_rate": 1,
				"cost_center": "_Test Cost Center - _TC",
			},
		)

		calculate_taxes_and_totals(so)
		return so

	def test_inclusive_tax_net_total_adds_up_to_grand_total(self):
		"""The residual lost when each row's net amount is rounded must be diffused across
		the rows. 2 x 379 @ 21% inclusive: net 758 / 1.21 = 626.4463 -> 626.45, VAT 131.55.
		Rounding each row on its own gives 313.22 + 313.22 = 626.44, one cent short."""
		so = self.make_inclusive_tax_order([(1, 379), (1, 379)])

		self.assertEqual([item.net_amount for item in so.items], [313.22, 313.23])
		self.assertEqual(so.total, 758.0)
		self.assertEqual(so.net_total, 626.45)
		self.assertEqual(so.taxes[0].tax_amount, 131.55)
		self.assertEqual(so.taxes[0].total, 758.0)
		self.assertEqual(so.grand_total, 758.0)
		self.assertEqual(flt(so.net_total + so.total_taxes_and_charges, 2), so.grand_total)

	def test_inclusive_tax_totals_dont_depend_on_line_split(self):
		"""One line of qty 2 and two lines of qty 1 must produce the same totals."""
		single = self.make_inclusive_tax_order([(2, 379)])
		split = self.make_inclusive_tax_order([(1, 379), (1, 379)])

		self.assertEqual(single.net_total, split.net_total)
		self.assertEqual(single.taxes[0].tax_amount, split.taxes[0].tax_amount)
		self.assertEqual(single.grand_total, split.grand_total)

	def test_inclusive_tax_residual_beyond_grand_total_diff_threshold(self):
		"""`grand_total_diff` only compensates a residual up to 0.5 of the last decimal.
		With 20 rows the residual reached 0.06, the compensation was dropped entirely and
		the grand total came out at 7579.94 against a line total of 7580.00."""
		so = self.make_inclusive_tax_order([(1, 379)] * 20)

		self.assertEqual(so.total, 7580.0)
		self.assertEqual(so.net_total, 6264.46)
		self.assertEqual(so.taxes[0].tax_amount, 1315.54)
		self.assertEqual(so.grand_total, 7580.0)
		self.assertEqual(flt(so.net_total + so.total_taxes_and_charges, 2), so.grand_total)
