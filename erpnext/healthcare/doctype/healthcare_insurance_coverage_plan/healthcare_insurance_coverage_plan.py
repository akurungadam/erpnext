# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import getdate, nowdate
from frappe.model.document import Document

class HealthcareInsuranceCoveragePlan(Document):
    def validate(self):
        self.validate_insurance_company()

    def validate_insurance_company(self):
        contract = frappe.db.exists('Healthcare Insurance Contract', {
                        'insurance_company': self.insurance_company,
                        'end_date':(">=", nowdate()),
                        'is_active': 1,
                        'docstatus': 1
                })

        if not contract:
            frappe.throw(_('No valid Insurance Contract found for {} {}')
                .format(frappe.bold(self.insurance_company), frappe.bold(self.insurance_company_name)),
                title=_('Did not Save'))
