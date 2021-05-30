# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import get_link_to_form, getdate
from frappe.model.document import Document
from erpnext.healthcare.doctype.healthcare_insurance_company.healthcare_insurance_company import has_active_contract

class HealthcareInsuranceSubscription(Document):
	def validate(self):
		# check if a contract exist for the insurance company
		if not has_active_contract(self.insurance_company):
			frappe.throw(_('No active contracts found for Insurance Company {0}')
				.format(self.insurance_company))

		self.validate_expiry_date()
		self.validate_subscription_overlap()
		self.set_title()

	def validate_expiry_date(self):
		if getdate(self.policy_expiry_date) < getdate():
			frappe.throw(_('Expiry Date for the Subscription cannot be a past date'))

	def validate_subscription_overlap(self):
		insurance_subscription = frappe.db.exists('Healthcare Insurance Subscription', {
			'patient': self.patient,
			'docstatus': 1,
			'policy_expiry_date': ['<=', self.policy_expiry_date],
			'insurance_company': self.insurance_company,
			'insurance_coverage_plan': self.insurance_coverage_plan or ''
		})
		if insurance_subscription:
			frappe.throw(_('Patient {0} already has an active insurance subscription {1} with the coverage plan {2} for this period').format(
				frappe.bold(self.patient), get_link_to_form('Healthcare Insurance Subscription', insurance_subscription),
				frappe.bold(self.healthcare_insurance_coverage_plan)), title=_('Duplicate'))

	def set_title(self):
		self.title = _('{0} - {1}').format(self.patient_name or self.patient, self.insurance_policy_number)

def is_valid_insurance_subscription(subscription, company=None, on_date=None):
	if subscription:
		insurance_co, policy_expiry = frappe.db.get_value('Healthcare Insurance Subscription', subscription, ['insurance_company', 'policy_expiry_date'])

		if getdate(policy_expiry) >= (getdate(on_date) or getdate()) and has_active_contract(insurance_co, company, on_date):
			return True
	return False