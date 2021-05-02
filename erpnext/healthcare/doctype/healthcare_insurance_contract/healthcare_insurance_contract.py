# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import getdate, nowdate
from frappe.model.document import Document

class HealthcareInsuranceContract(Document):
	def on_submit(self):
		if self.is_active:
			self.validate_contract_overlap()

	def on_update_after_submit(self):
		if self.is_active:
			self.validate_contract_overlap()

	def validate_contract_overlap(self):
		active_contract = frappe.db.exists('Healthcare Insurance Contract', {
			'name': ['!=', self.name],
			'insurance_company': self.insurance_company,
			'is_active': 1,
			'docstatus': 1,
			'start_date': ["<=", getdate(nowdate())],
			'end_date': [">=", getdate(nowdate())]
		})
		if active_contract:
			frappe.throw(_('An active contract {} already exists').format(frappe.bold(active_contract)))
