# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import getdate, flt, get_link_to_form
from frappe.model.document import Document
from erpnext.healthcare.doctype.healthcare_service_insurance_coverage.healthcare_service_insurance_coverage import get_insurance_coverage
from erpnext.healthcare.doctype.healthcare_insurance_subscription.healthcare_insurance_subscription import is_insurance_policy_valid, get_insurance_price_lists

class CoverageNotFoundError(frappe.ValidationError): pass
class HealthcareInsuranceClaim(Document):
	def validate(self):
		self.validate_insurance_policy()
		self.set_and_validate_item_code()

		if self.status in ['Draft', 'Approved']:
			if not self.set_insurance_coverage():
				self.status = 'Draft'
				raise CoverageNotFoundError

			if self.set_insurance_price_list_rate() and self.set_insurance_claim_details():
				self.status = 'Approved' if self.mode_of_approval == 'Automatic' else 'Draft'
			else:
				self.status = 'Draft'

		self.validate_status()
		self.validate_invoice_details()
		self.set_title()

		# show alert if mode of approval is Manual
		if self.mode_of_approval == 'Manual':
			frappe.msgprint(_('Manual approval required for Insurance Claim {}').format(self.name),
				alert=True, indicator='orange')

	def validate_insurance_policy(self):
		if not self.insurance_subscription:
			frappe.throw(_('Patient Insurance Policy is required to create Insurance Claim'), title=_('Missing Insurance Policy'))

		if not is_insurance_policy_valid(self.insurance_subscription, self.posting_date, self.company): # also checks for valid contract
			frappe.throw(_('Patient Insurance Policy {} is not valid as on {}').format(
				frappe.bold(self.insurance_subscription), self.posting_date), title=_('Invalid Insurance Policy'))

	def validate_status(self):
		if self.status == 'Approved' and (not self.service_coverage or self.coverage_amount <= 0):
			frappe.throw('Insurance Claim cannot be Approved without a valid Coverage Amount')

	def validate_invoice_details(self):
		if self.invoiced_qty > self.qty or self.invoiced_amount > self.patient_payable:
			frappe.msgprint(_('Insurance Claim Detail Invoiced Quantity / Invoiced Amount cannot be more than Approved Quantity {} or Amount {}'),format(
				self.invoiced_qty, self.invoiced_amount))

	def before_submit(self):
		if self.status not in ['Approved', 'Rejected']:
			frappe.throw(_('Insurance Claims can only be submitted with Status <b>Approved</b> or <b>Rejected</b>'), title=_('Not Allowed'))

	def on_submit(self):
		if not self.flags.silent:
			frappe.msgprint(_('Insurance Claim {} - {}<br>Discount: {}%, Coverage: {}%').format(
				self.name, frappe.bold(self.status), self.discount, self.coverage),
				alert=True, indicator='green' if self.status == 'Approved' else 'orange')

		self.flags.silent = False

	def on_update_after_submit(self):
		self.invoiced_qty = sum(d.get('invoice_qty') or 0 for d in self.insurance_claim_details)
		self.invoiced_amount = sum(d.get('claim_amount') or 0 for d in self.insurance_claim_details)

	def before_cancel(self):
		not_allowed = ['Invoiced', 'Partially Invoiced' ,'Paid', 'Rejected', 'Payment Rejected']
		if self.billing_status in not_allowed:
			frappe.throw(_('Cannot cancel Insurance Claim with Claim Status {}').format(', '.join(not_allowed)),
			title=_('Not Allowed'))

		# unlink from linked doctype (Appointment / Encounter, HSO, IP Record)
		doc_link = self.get_service_doctype_link()
		if doc_link and doc_link.get('link_dt') and doc_link.get('link_dn'):
			frappe.db.set_value(doc_link.get('link_dt'), doc_link.get('link_dn'), {'insurance_claim': '', 'claim_status': ''})
			frappe.msgprint(_('Insurance Claim unlinked from {0} {1}').format(doc_link.get('link_dt'), frappe.bold(doc_link.get('link_dn'))),
				alert=True, indicator='info')

		self.status = 'Cancelled'

	def set_title(self):
		self.title = f'{self.patient_name} - {self.template_dn} - {self.status}'

	def set_and_validate_item_code(self):
		# reset item_code only if service template selected
		if self.template_dt and self.template_dn and self.template_dt not in ['Appointment Type']:
			self.item_code = frappe.db.get_value(self.template_dt, self.template_dn, 'item')

		if not self.item_code:
			frappe.throw(_('Service Template or Item is required to create Insurance Claim'), title=_('Missing Mandatory Fields'))

	def set_insurance_coverage(self):
		'''
		Set Insurance coverage for the Item and set coverage details
		Retruns True if if Insurance Coverage present for template / item_code else show alert and return False
		'''
		coverage_detail = get_insurance_coverage(
			item_code=self.item_code,
			template_dt=self.template_dt,
			template_dn=self.template_dn,
			on_date=self.posting_date,
			coverage_plan=self.insurance_coverage_plan)

		if not coverage_detail:
			self.reset_claim_details()
			frappe.msgprint(_('Insurance Coverage not found for {}.').format(
				self.item_code), alert=True, indicator='warning')
			return False
		
		self.service_coverage = coverage_detail.get('name')
		self.insurance_coverage_plan = coverage_detail.get('insurance_coverage_plan')
		self.mode_of_approval = coverage_detail.get('mode_of_approval')
		self.coverage = coverage_detail.get('coverage')
		self.discount = coverage_detail.get('discount')

		if coverage_detail.get('valid_till') and getdate(coverage_detail.get('valid_till')) < getdate(self.claim_validity_end_date):
			self.claim_validity_end_date = coverage_detail.get('valid_till')

		return True

	def set_insurance_price_list_rate(self):
		'''
		Set Insurance price list and price list rate for the Item
		Retruns True if Item Price found else show alert and return False
		'''
		price_list_detail = get_insurance_price_list_rate(self.item_code, self.insurance_subscription, self.company)
		if not price_list_detail or (price_list_detail and not price_list_detail.get('price_list_rate')):
			frappe.msgprint(_('Item Price for Item {} not found').format(get_link_to_form(self.item_code)),
				alert=True, indicator='error')
			return False

		self.price_list_rate = price_list_detail.get('price_list_rate')
		self.price_list = price_list_detail.get('price_list')

		return True

	def set_insurance_claim_details(self):
		'''
		Set coverage details (coverage amount, patient payable) based on Insurance Coverage and Item Price
		Retruns True if coverage amount calculated else show alert and return False
		'''
		if self.discount and self.discount > 0:
			self.discount_amount = (flt(self.price_list_rate) * flt(self.discount) * 0.01) * flt(self.qty)

		self.amount = (flt(self.price_list_rate) * flt(self.qty)) - flt(self.discount_amount) if self.discount_amount else 0
		if self.coverage and self.coverage > 0:
			self.coverage_amount = flt(self.amount) * flt(self.coverage) * 0.01
			self.patient_payable = flt(self.amount) - flt(self.coverage_amount)

		if self.coverage_amount <= 0:
			frappe.msgprint(_('Error calculating Coverage for Insurance Claim {}. \
				Please verify Coverage for Item and then try saving Insurance Claim again').format(self.name),
				alert=True, indicator='error')
			return False

		return True

	def reset_claim_details(self):
		self.service_coverage = ''
		self.mode_of_approval = 'Manual'
		self.coverage = 0
		self.discount = 0
		self.amount = 0
		self.coverage_amount = 0
		self.patient_payable = 0

	def get_service_doctype_link(self):
		'''
		Returns the linked service dt, dn
		'''
		if self.template_dt == 'Appointment Type':
			link_name = frappe.db.exists('Patient Appointment', {'insurance_claim': self.name})
			if link_name:
				return {'link_dt': 'Patient Appointment', 'link_dn': link_name}

			link_name = frappe.db.exists('Patient Encounter', {'insurance_claim': self.name})
			if link_name:
				return {'link_dt': 'Patient Encounter', 'link_dn': link_name}

		elif self.template_dt == 'Healthcare Service Unit Type':
			link_name = frappe.db.exists('Inpatient Record', {'insurance_claim': self.name})
			return {'link_dt': 'Inpatient Record', 'link_dn': link_name}

		else:
			link_name = frappe.db.exists('Healthcare Service Order', {'insurance_claim': self.name})
			return {'link_dt': 'Healthcare Service Order', 'link_dn': link_name}


def make_insurance_claim(patient, policy, company, template_dt=None, template_dn=None, item_code=None, qty=1):
	'''
	Inserts a new Insurance Claim for the service
	If claim status is Approved, Submits the claim
	Returns claim name and status if Insurance Claim inserted
	'''
	if not (template_dt and template_dn) and not item_code:
		return None

	claim = frappe.new_doc('Healthcare Insurance Claim')
	claim.status = 'Draft'
	claim.billing_status = 'Pending'
	claim.patient = patient
	claim.company = company
	claim.posting_date = getdate()

	claim.template_dt = template_dt
	claim.template_dn = template_dn
	claim.item_code = item_code if item_code else frappe.db.get_value(template_dt, template_dn, 'item') #TODO: verify fieldname item
	claim.qty = qty

	claim.insurance_subscription = policy
	policy_details = frappe.db.get_value('Healthcare Insurance Subscription', policy, ['policy_expiry_date', 'insurance_coverage_plan'], as_dict=True)
	claim.claim_validity_end_date = policy_details.get('policy_expiry_date')
	claim.insurance_coverage_plan = policy_details.get('insurance_coverage_plan')

	try:
		claim.insert(ignore_permissions=True)
	except CoverageNotFoundError:
		return None

	if claim.status == 'Approved':
		claim.submit()

	return {
		'claim': claim.name,
		'claim_status': claim.status
	}

def get_insurance_price_list_rate(item_code, policy, company=None):
	'''
	Return price_list_rate and price_list based on Patient's Insurance Policy
	'''
	#TODO: fix 6 db fetch
	insurance_price_lists = get_insurance_price_lists(policy, company) #TODO: 2 db fetch

	if insurance_price_lists:
		if insurance_price_lists.get('plan_price_list'):
			price_list_rate = get_item_price_list_rate(insurance_price_lists.get('plan_price_list'), item_code) #TODO: 2 db fetch
			if price_list_rate:
				return {'price_list': insurance_price_lists.get('plan_price_list'), 'price_list_rate': price_list_rate}

		if insurance_price_lists.get('default_price_list'):
			price_list_rate = get_item_price_list_rate(insurance_price_lists.get('default_price_list'), item_code) #TODO
			if price_list_rate:
				return {'price_list': insurance_price_lists.get('default_price_list'), 'price_list_rate': price_list_rate}

		# Fall back to selling price list rate from selling settings
		selling_price_list = frappe.db.get_single_value('Selling Settings', 'selling_price_list') #TODO: required?
		if selling_price_list:
			price_list_rate = get_item_price_list_rate(selling_price_list, item_code)
			if price_list_rate:
				return {'price_list': selling_price_list, 'price_list_rate': price_list_rate}


def get_item_price_list_rate(price_list, item_code):
	# use get_item_details
	item_price = frappe.db.exists('Item Price', {'price_list': price_list, 'item_code': item_code})
	if item_price:
		return frappe.db.get_value('Item Price', item_price, 'price_list_rate')


@frappe.whitelist()
def create_insurance_coverage(doc): #TODO: fix fieldnames
	from six import string_types
	import json

	if isinstance(doc, string_types):
		doc = json.loads(doc)
		doc = frappe._dict(doc)

	coverage_plan = frappe.db.get_value('Healthcare Insurance Subscription', doc.insurance_subscription, 'healthcare_insurance_coverage_plan')

	coverage_service = frappe.new_doc('Healthcare Service Insurance Coverage')
	coverage_service.coverage_based_on = doc.coverage_based_on
	coverage_service.healthcare_insurance_coverage_plan = coverage_plan
	coverage_service.insurance_coverage_plan_name = frappe.db.get_value('Healthcare Insurance Coverage Plan', coverage_plan, 'coverage_plan_name')


	if doc.coverage_based_on == 'Service':
		coverage_service.healthcare_service = doc.template_type
		coverage_service.healthcare_service_template = doc.template_dn

	elif doc.coverage_based_on == 'Medical Code':
		coverage_service.medical_code = doc.medical_code

	elif doc.coverage_based_on == 'Item':
		coverage_service.item = doc.item_code

	coverage_service.coverage = doc.coverage
	coverage_service.discount = doc.discount
	coverage_service.start_date = doc.posting_date or getdate()
	coverage_service.end_date = doc.approval_validity_end_date
	return coverage_service
