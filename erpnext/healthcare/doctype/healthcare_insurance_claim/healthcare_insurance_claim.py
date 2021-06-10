# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import getdate, flt, get_link_to_form
from frappe.model.document import Document
from erpnext.healthcare.doctype.healthcare_service_insurance_coverage.healthcare_service_insurance_coverage import get_service_insurance_coverage
from erpnext.healthcare.doctype.healthcare_insurance_company.healthcare_insurance_company import get_insurance_party_details
from erpnext.healthcare.doctype.healthcare_insurance_subscription.healthcare_insurance_subscription import is_valid_insurance_subscription, get_insurance_price_lists
from erpnext.healthcare.doctype.appointment_type.appointment_type import get_service_item_based_on_department


class HealthcareInsuranceClaim(Document):
	def validate(self):
		'''
		insurance_details = get_insurance_details(doc, service_type, service_template, billing_item)
		set insurance coverage fields
		'''
		self.validate_insurance_policy()
		self.validate_service()
		self.validate_status()

		if self.status == 'Draft':
			if self.set_insurance_coverage_details() and self.mode_of_approval == 'Automatic' and self.coverage_amount > 0:
				self.status = 'Approved'

	def before_submit(self):
		if self.status != 'Approved':
			frappe.throw(_('You can only submit Insurance Claims in Approved status')) #TODO: MSG

	def on_update(self):
		self.update_link_claim_status()

	def after_insert(self):
		self.update_link_claim_status()

	def on_update_after_submit(self):
		self.update_link_claim_status()

	def before_cancel(self):
		not_allowed = ['Invoiced', 'Partially Invoiced' ,'Paid', 'Rejected', 'Payment Rejected']
		if self.status in not_allowed:
			frappe.throw(_('Cannot cancel Insurance Claim in status {}').format(', '.join(not_allowed)))

	def on_cancel(self):
		if self.status != 'Invoiced':
			self.update_link_claim_status(cancel=True)

	def validate_status(self):
		if self.status == 'Approved' and self.coverage_amount <= 0:
			frappe.throw('Insurance Claim cannot be Approved without a valid Coverage Amount')

	def validate_insurance_policy(self):
		if not self.insurance_subscription:
			frappe.throw('Insurance Subscription is mandatory to create Insurance Claim') #TODO: MSG

		if not is_valid_insurance_subscription(self.insurance_subscription, self.company, self.posting_date): # also checks for valid contract
			frappe.throw(_('Insurance Subscription {} is not valid as on {}').format(frappe.bold(self.insurance_subscription), self.posting_date))

	def validate_service(self):
		if not (self.service_template_doctype and self.service_template) or not self.item_code:
			frappe.throw(_('Service Template or Item is mandatory to create Insurance Claim')) #TODO: MSG

	def update_link_claim_status(self, cancel=False):
		link_name = frappe.db.exists(self.link_doctype, {'insurance_claim': self.name})

		if link_name and not cancel:
			frappe.db.set_value(self.link_doctype, link_name, 'claim_status', self.status)
		elif link_name and cancel:
			frappe.db.set_value(self.link_doctype, link_name, {'insurance_claim': '', 'claim_status': ''})
			frappe.msgprint(_('Insurance Claim unlinked from {0} {1}').format(self.link_doctype, frappe.bold(link_name)))

	def set_invoice_details(self, qty, amount, cancel=False):
		if not cancel:
			if self.status == 'Invoiced':
				frappe.throw(_('Insurance Claim {} already in Invoiced status').format(frappe.bold(self.name))) #TODO: MSG
			self.claimed_qty += qty
			self.total_claim_amount += amount
		else:
			self.claimed_qty -= qty # if self.claimed_qty > 0 else 0
			self.total_claim_amount -= amount

		if self.claimed_qty > self.qty:
			frappe.throw(_('Invoiced Quantity cannot be more than Approved Quantity {}').format(frappe.bold(self.qty))) #TODO: MSG

		# Update status
		if self.claimed_qty < self.qty:
			self.status = 'Partially Invoiced'
		else:
			self.status = 'Invoiced'

		self.save()

	def set_insurance_coverage_details(self):
		coverage_detail = get_service_insurance_coverage(self.service_template_doctype, self.service_template,
			self.item_code, self.posting_date, self.insurance_coverage_plan)

		if not coverage_detail:
			frappe.msgprint(_('No matching Insurance Coverage found for Service {} - {} or Item {}').format(
				self.service_template_doctype, self.service_template, self.item_code),
				alert=True, indicator='red')
			return False

		self.service_coverage = coverage_detail.get('name')
		self.insurance_coverage_plan = coverage_detail.get('insurance_coverage_plan')
		self.mode_of_approval = coverage_detail.get('mode_of_approval')
		self.coverage = coverage_detail.get('coverage')
		self.discount = coverage_detail.get('discount')

		if coverage_detail.get('valid_till') and getdate(coverage_detail.get('valid_till')) < getdate(self.claim_validity_end_date):
			self.claim_validity_end_date = coverage_detail.get('valid_till')

		price_list_detail = get_insurance_price_list_rate(self.item_code, self.insurance_subscription, self.company)
		if not price_list_detail:
			frappe.msgprint(_('Item Price for {} not found, saving Insurance Claim in Draft mode').format(self.item_code),
				alert=True, indicator='red')
			return False

		self.price_list_rate = price_list_detail.get('price_list_rate')
		self.price_list = price_list_detail.get('price_list')

		if self.discount and self.discount > 0:
			self.discount_amount = (flt(self.price_list_rate) * flt(self.discount) * 0.01) * flt(self.qty)

		self.amount = (flt(self.price_list_rate) * flt(self.qty)) - flt(self.discount_amount) if self.discount_amount else 0
		if self.coverage and self.coverage > 0:
			self.coverage_amount = flt(self.amount) * flt(self.coverage) * 0.01

		if self.coverage_amount <= 0:
			frappe.msgprint(_('Error calculating Coverage, please check Insurance Claim {}').format(self.name),
				alert=True, indicator='red')
			return False

		return True


def make_insurance_claim(doc):
	'''
	param: Healthcare Service Order or any Service document
	Creates a new 'Draft' Insurance Claim for the service
	'''
	claim = frappe.new_doc('Healthcare Insurance Claim')
	claim.company = doc.company
	claim.posting_date = getdate() #TODO: doc.date
	claim.patient = doc.patient

	claim.insurance_subscription = doc.insurance_subscription
	policy_details = frappe.db.get_value('Healthcare Insurance Subscription', doc.insurance_subscription, 
		['policy_expiry_date', 'insurance_coverage_plan'], as_dict=True)

	claim.claim_validity_end_date = policy_details.get('policy_expiry_date')
	claim.insurance_coverage_plan = policy_details.get('insurance_coverage_plan')

	claim.qty = doc.quantity if doc.get('quantity') else 1

	claim.link_doctype = doc.doctype
	template_detail = get_template_details(doc)
	claim.service_template_doctype = template_detail.get('template_dt')
	claim.service_template = template_detail.get('template_dn')
	claim.item_code = template_detail.get('item_code')

	claim.status = 'Draft'
	claim.insert(ignore_permissions=True)

	return claim.name


def get_insurance_price_list_rate(item_code, policy, company=None):
	'''
	Return price_list_rate and price_list based on Patient's Insurance Policy
	'''
	#TODO: 6 db calls
	insurance_price_lists = get_insurance_price_lists(policy, company) #TODO: 2 db calls inside this

	if insurance_price_lists:
		if insurance_price_lists.get('plan_price_list'):
			price_list_rate = get_item_price_list_rate(insurance_price_lists.get('plan_price_list'), item_code) #TODO: 2 db calls inside this
			if price_list_rate:
				return {'price_list': insurance_price_lists.get('plan_price_list'), 'price_list_rate': price_list_rate}

		if insurance_price_lists.get('default_price_list'):
			price_list_rate = get_item_price_list_rate(insurance_price_lists.get('default_price_list'), item_code) #TODO
			if price_list_rate:
				return {'price_list_rate': price_list_rate, 'price_list': insurance_price_lists.get('default_price_list')}

		# No price list rate found, retrun selling price list rate
		selling_price_list = frappe.db.get_single_value('Selling Settings', 'selling_price_list') #TODO
		if selling_price_list:
			price_list_rate = get_item_price_list_rate(selling_price_list, item_code)
			if price_list_rate:
				return {'price_list_rate': price_list_rate, 'price_list': selling_price_list}


def get_item_price_list_rate(price_list, item_code):
	# use get_item_details
	item_price = frappe.db.exists('Item Price', {'price_list': price_list, 'item_code': item_code})
	if item_price:
		return frappe.db.get_value('Item Price', item_price, 'price_list_rate')


@frappe.whitelist()
def get_template_details(doc):
	'''
	Returns a dict with
		template_dt: healthcare template doctype (Lab Test Template, Therapy Type)
		template_dn: template name
		item_code billable item linked to template

	param: Any Service document (Lab test, Therapy Session etc.) or Healthcare Service Order document
	'''
	if doc.doctype == 'Healthcare Service Order':
		template_detail = frappe.db.get_value(doc.doctype, doc.name, ['order_doctype as template_dt', 'order_template as template_dn'], as_dict=True)
	elif doc.doctype == 'Lab Test':
		template_detail = {'template_dt': 'Lab Test Template', 'template_dn': doc.template}
	elif doc.doctype == 'Clinical Procedure':
		template_detail = {'template_dt': 'Clinical Procedure Template', 'template_dn': doc.procedure_template}
	elif doc.doctype == 'Therapy Session':
		template_detail = {'template_dt': 'Therapy Type', 'template_dn': doc.therapy_type}
	elif doc.doctype == 'Therapy Plan':
		template_detail = {'template_dt': 'Therapy Plan Template', 'template_dn': doc.therapy_plan_template}
	elif doc.doctype in ['Patient Appointment', 'Patient Encounter']:
		template_detail = {'template_dt': 'Appointment Type', 'template_dn': doc.appointment_type}
	else: #TODO: remove
		template_detail = {'template_dt': doc.template_dt, 'template_dn': doc.template_name}

	# fetch item_code based on template
	print(doc.doctype, template_detail)
	if template_detail.get('template_dt') == 'Appointment Type':
		item_detail = get_service_item_based_on_department(doc.appointment_type, doc.department if doc.doctype == 'Patient Appointment' else doc.medical_department)
		template_detail.update({'item_code':
			item_detail.get('inpatient_visit_charge_item') if doc.inpatient_record else item_detail.get('op_consulting_charge_item')
		})
	elif template_detail.get('template_dt') == 'Therapy Plan Template':
		template_detail.update(frappe.db.get_value(template_detail.get('template_dt'), template_detail.get('template_dn'), ['linked_item as item_code'], as_dict=True))
	else:
		template_detail.update(frappe.db.get_value(template_detail.get('template_dt'), template_detail.get('template_dn'), ['item as item_code'], as_dict=True))

	return template_detail


@frappe.whitelist()
def create_insurance_coverage(doc):
	from six import string_types
	import json

	if isinstance(doc, string_types):
		doc = json.loads(doc)
		doc = frappe._dict(doc)

	coverage_plan = frappe.db.get_value('Healthcare Insurance Subscription',
		doc.insurance_subscription, 'healthcare_insurance_coverage_plan')

	coverage_service = frappe.new_doc('Healthcare Service Insurance Coverage')
	coverage_service.coverage_based_on = doc.coverage_based_on
	coverage_service.healthcare_insurance_coverage_plan = coverage_plan
	coverage_service.insurance_coverage_plan_name = frappe.db.get_value('Healthcare Insurance Coverage Plan',
		coverage_plan, 'coverage_plan_name')


	if doc.coverage_based_on == 'Service':
		coverage_service.healthcare_service = doc.template_type
		coverage_service.healthcare_service_template = doc.service_template

	elif doc.coverage_based_on == 'Medical Code':
		coverage_service.medical_code = doc.medical_code

	elif doc.coverage_based_on == 'Item':
		coverage_service.item = doc.item_code

	coverage_service.coverage = doc.coverage
	coverage_service.discount = doc.discount
	coverage_service.start_date = doc.posting_date or getdate()
	coverage_service.end_date = doc.approval_validity_end_date
	return coverage_service
