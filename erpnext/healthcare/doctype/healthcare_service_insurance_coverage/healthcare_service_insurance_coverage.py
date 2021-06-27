# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import getdate, get_link_to_form, getdate
from frappe.model.document import Document

class CoverageOverlapError(frappe.ValidationError): pass

class HealthcareServiceInsuranceCoverage(Document):
    def validate(self):
        if self.is_active:
            self.validate_coverage_details()
            self.validate_dates()
            self.validate_overlaps()

        self.set_title()

    def validate_coverage_details(self):
        if self.coverage <= 0 or self.discount <= 0:
            frappe.throw(_('Invalid Coverage / Discount percentage'))

    def validate_dates(self):
        if self.valid_from and self.valid_till:
            if self.valid_from > self.valid_till:
                frappe.throw(_('Valid From date cannot be after Valid Till date'))

    def validate_overlaps(self):
        conditions = """ifnull(is_active, 0) = 1 and name != '{name}'""".format(name=self.name)
        conditions += """ and ifnull(insurance_coverage_plan, '') = {}""".format(
                frappe.db.escape(self.insurance_coverage_plan))

        # Period
        if self.valid_from and self.valid_till:
            conditions += """ and ((valid_from > '{valid_from}' and valid_from < '{valid_till}') or
				(valid_till > '{valid_from}' and valid_till < '{valid_till}') or
				('{valid_from}' > valid_from and '{valid_from}' < valid_till) or
				('{valid_from}' = valid_from and '{valid_till}' = valid_till))
			""".format(valid_from=self.valid_from, valid_till=self.valid_till)

        elif self.valid_from and not self.valid_till:
            conditions += """ and valid_till > '{valid_from}'""".format(valid_from = self.valid_from)

        elif self.valid_till and not self.valid_from:
            conditions += """ and valid_from < '{valid_till}'""".format(valid_till = self.valid_till)

        # Coverage based on
        service_filters = self.get_service_filters()
        for field in service_filters:
            conditions += """ and {} = {}""".format(field, frappe.db.escape(service_filters.get(field)))

        overlap = frappe.db.sql('''
			SELECT name
			FROM `tabHealthcare Service Insurance Coverage`
			WHERE {}
		'''.format(conditions), as_dict=1)

        if overlap:
            frappe.throw(_('Coverage overlaps with {}').format(get_link_to_form(self.doctype, overlap[0].name)),
				CoverageOverlapError, title=_('Not Allowed'))

    def get_service_filters(self):
        if self.coverage_based_on == 'Service':
            return {'healthcare_service': self.healthcare_service, 'healthcare_service_template' : self.healthcare_service_template}

        # elif self.coverage_based_on == 'Medical Code':
        #     return {'medical_code_standard': self.medical_code_standard, 'medical_code': self.medical_code}

        # elif self.coverage_based_on == 'Item Group':
        #     return {'item_group': self.item_group}

        elif self.coverage_based_on == 'Item':
            return {'item': self.item}

    def set_title(self):
        if self.coverage_based_on == 'Service':
            self.title = _('{} - {}').format(self.healthcare_service_template, self.healthcare_service)

        elif self.coverage_based_on == 'Item':
            self.title = _('{} - {}').format(self.item, self.coverage_based_on)

        # elif self.coverage_based_on == 'Item Group':
        #     self.title = _('{} - {}').format(self.item_group, self.coverage_based_on)

        # elif self.coverage_based_on == 'Medical Code':
        #     self.title = _('{} - {}').format(self.medical_code, self.medical_code_standard)


# def get_service_insurance_coverage(item_code=None, on_date=None, coverage_plan=None):
def get_service_insurance_coverage(service_template_type, service_template, item_code=None, on_date=None, coverage_plan=None):
    '''
    Find and return details of insurance coverage for a Healthcare Service
    Returns a dict with name, mode_of_approval, coverage, discount form Healthcare Insurance Coverage

    Arguments-
    insurance_subscription: Insurance Subscription should be valid
    company: Insurance Company should have a valid contract with Company
    service_template_type: one of 'Appointment Type', 'Clinical Procedure Template', 'Therapy Type', 'Medication', 'Lab Test Template', 'Healthcare Service Unit Type'
    service_template: any of the Healthcare Service Templates
    item_code: only used if service_template_type is Appointment Type (TODO: fix)

    NOTE:
    If coverage for the requested service is not found, coverage for requested service's Medical Code / Item / Item Group is returned
    Initial result set from db is ordered by valid_from date, record nearest to queried date is returned
    '''
    if not on_date:
        on_date = getdate()

    conditions = """ifnull(is_active, 0) = 1 and
		ifnull(insurance_coverage_plan, '') = {}""".format(frappe.db.escape(coverage_plan or ''))

    conditions += """ and ('{}' between
		ifnull(valid_from, '2000-01-01') and ifnull(valid_till, '2500-12-31'))""".format(getdate(on_date) or getdate())

    conditions += """ and ((ifnull(healthcare_service, '') = {} and ifnull(healthcare_service_template, '') = {})""".format(
                    frappe.db.escape(service_template_type or ''), frappe.db.escape(service_template or ''))

    # Also add conditions based on service's medical code / item etc.
    service_details = {}
    if service_template_type == 'Appointment Type' and item_code:
        service_details['item_code'] = item_code
        service_details['item_group'] = frappe.db.get_value('Item', item_code, 'item_group')
    else:
        field_list = get_service_template_field_list(service_template_type)
        service_details = frappe.db.get_value(service_template_type, service_template, field_list, as_dict=1)

    # if service_details.get('medical_code_standard') and service_details.get('medical_code'):
    #     conditions += """ or (ifnull(medical_code_standard, '') = {} and ifnull(medical_code, '') = {})""".format(
    #             frappe.db.escape(service_details.get('medical_code_standard') or ''), frappe.db.escape(service_details.get('medical_code') or ''))

    # if service_details.get('item_code'):
    if service_details.get('item_code') or service_details.get('item_group'):
        conditions += """ or (ifnull(item, '') = {} or ifnull(item_group, '') = {}))""".format(
                frappe.db.escape(service_details.get('item_code') or ''), frappe.db.escape(service_details.get('item_group') or ''))

    all_coverages = frappe.db.sql('''
			SELECT
				name,
				healthcare_service_template,
				medical_code, item,
				item_group,
				valid_from,
				valid_till,
				insurance_coverage_plan,
				mode_of_approval,
				coverage,
				discount
			FROM `tabHealthcare Service Insurance Coverage`
			WHERE {}
			ORDER BY valid_from DESC
		'''.format(conditions), as_dict=1)

    #TODO: extract method
    if all_coverages and len(all_coverages) > 0:
        coverages = list(filter(lambda d: d['healthcare_service_template'] == service_template, all_coverages))
        if len(coverages) > 0:
            return coverages[0]

        # if service_details.get('medical_code'):
        #     coverages = list(filter(lambda d: d['medical_code'] == service_details.get('medical_code'), all_coverages))
        #     if len(coverages) > 0:
        #         return coverages[0]

        if service_details.get('item_code'):
            coverages = list(filter(lambda d: d['item'] == service_details.get('item_code'), all_coverages))
            if len(coverages) > 0:
                return coverages[0]

        # if service_details.get('item_group'):
        #     coverages = list(filter(lambda d: d['item_group'] == service_details.get('item_group'), all_coverages))
        #     if len(coverages) > 0:
        #         return coverages[0]

    return None

def get_service_template_field_list(service_template_type):
    # field_list = ['medical_code', 'medical_code_standard'] #TODO: Fix
    field_list = []
    if service_template_type == 'Lab Test Template':
        field_list.extend(['item as item_code', 'lab_test_group as item_group'])
    else:
        field_list.extend(['item_code', 'item_group'])
    return field_list
