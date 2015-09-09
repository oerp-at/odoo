# -*- encoding: utf-8 -*-
#############################################################################
#
#    Copyright (c) 2007 Martin Reisenhofer <martinr@funkring.net>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import time
import datetime

from openerp.osv import fields, osv
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT
from openerp.tools.translate import _
from openerp.addons.at_base import util

import openerp.addons.decimal_precision as dp

class sale_order_line(osv.Model):

    def _quotation_all(self, cr, uid, ids, field_name, arg, context=None):
        res = dict.fromkeys(ids)
        for line in self.browse(cr, uid, ids, context=context):
            sent_all = True
            for quotation in line.quotation_ids:
                if not quotation.quot_sent:
                    sent_all = False
                    break
            res[line.id] = sent_all
        return res

    def start_quotation(self, cr, uid, ids, context=None):
        if not ids:
            return True

        purchase_order_obj = self.pool["purchase.order"]
        purchase_line_obj = self.pool["purchase.order.line"]
        quotation_active = False

        for line in self.browse(cr, uid, ids, context=context):
            # check product
            product = line.product_id
            if not product:
                continue

            # check suppliers
            supplier_infos = product.seller_ids
            if not supplier_infos:
                continue

            order = line.order_id
            if not order:
                continue

            # process suppliers
            for supplier_info in supplier_infos:
                partner = supplier_info.name
                date_planned = util.dateToStr(datetime.datetime.today() + datetime.timedelta(line.delay or 0.0))

                purchase_line_vals = { "product_id" : product.id,
                                       "product_qty" : line.product_uom_qty,
                                       "sale_line_id" : line.id,
                                       "date_planned" : date_planned
                                     }

                purchase_order_vals = { "partner_id" : partner.id,
                                        "origin" : order.name,
                                        "sale_order_id" : order.id }

                # search existing
                purchase_line_id = purchase_line_obj.search_id(cr, uid, [("sale_line_id","=",line.id),("product_id","=",product.id),("partner_id","=",partner.id)])
                purchase_line = None
                purchase_line_state = None
                if purchase_line_id:
                    purchase_line = purchase_line_obj.browse(cr, uid, purchase_line_id, context=context)
                    # check readonly state
                    if purchase_line.state in ("confirmed","approved","done"):
                        continue

                    purchase_line_vals["price_unit"] = purchase_line.price_unit
                    purchase_line_state = purchase_line.state or 'draft'

                    purchase_order = purchase_line.order_id
                    if purchase_order:
                        purchase_order_vals["picking_type_id"] = purchase_order.picking_type_id.id

                else:
                    purchase_order_vals["picking_type_id"] = purchase_order_obj._get_picking_in(cr, uid, context=context)


                # reset selected if not update
                purchase_line_vals["quot_sent"] = False

                # onchange for order
                purchase_order_vals.update(purchase_order_obj.onchange_partner_id(cr, uid, [], partner.id, context=context)["value"])
                purchase_order_vals.update(purchase_order_obj.onchange_picking_type_id(cr, uid, [], purchase_order_vals.get("picking_type_id"), context=context)["value"])

                # onchaneg for line
                purchase_line_vals.update(purchase_line_obj.onchange_product_id(cr, uid, [],
                                                    purchase_order_vals.get("pricelist_id"),
                                                    purchase_line_vals.get("product_id"),
                                                    purchase_line_vals.get("product_qty"),
                                                    False,
                                                    purchase_order_vals.get("partner_id"),
                                                    date_planned=purchase_order_vals.get("date_planned"),
                                                    price_unit=purchase_line_vals.get("price_unit",False),
                                                    state=purchase_line_state,
                                                    context=context)["value"])
                purchase_line_vals["name"] = line.name

                purchase_line_vals["taxes_id"]=[(6,0,purchase_line_vals.get("taxes_id",[]))]

                # pop related fields
                purchase_order_vals.pop("related_location_id")
                purchase_order_vals.pop("related_usage")

                #  update or create
                if purchase_line_id:
                     purchase_order_vals["order_line"]=[(1,purchase_line_id,purchase_line_vals)]
                     purchase_order_obj.write(cr, uid, [purchase_line.order_id.id], purchase_order_vals, context=context)
                else:
                    purchase_order_vals["order_line"]=[(0,0,purchase_line_vals)]
                    purchase_order_obj.create(cr, uid, purchase_order_vals, context=context)

                quotation_active = True
                self.write(cr, uid, [line.id], {"quotation_active" : True} )

        return True

    def recreate_quotation(self, cr, uid, ids, context=None):
        return self.start_quotation(cr, uid, ids, context)

    def _product_id_change(self, cr, uid, res, flag, product_id, partner_id, lang, context=None):
        res = super(sale_order_line,self)._product_id_change(cr, uid, res, flag, product_id, partner_id, lang, context)
        res["value"].update({"quotation_active" : False})
        return res

    def send_mail_supplier(self, cr, uid, ids, context=None):
        purchase_line_obj = self.pool["purchase.order.line"]
        for line in self.browse(cr, uid, ids, context=context):
            purchase_line_ids = []

            quotations = line.quotation_ids
            if not quotations:
                continue

            for purchase_line in quotations:
                if not purchase_line.quot_sent:
                    purchase_line_ids.append(purchase_line.id)

        if not purchase_line_ids:
            raise osv.except_osv(_('Warning!'), _('E-mail was already sent to all supplier!'))

        # send mails
        return purchase_line_obj._send_supplier_mail(cr, uid, purchase_line_ids, context=context)

    def _quotation_id(self, cr, uid, ids, field_name, arg, context=None):
        res = dict.fromkeys(ids)
        purchase_line_obj = self.pool["purchase.order.line"]
        for line in self.browse(cr, uid, ids, context):
            supplier = line.supplier_id
            if supplier:
                res[line.id] = purchase_line_obj.search_id(cr, uid, [("sale_line_id","=",line.id),("partner_id","=",supplier.id)])
        return res

    def _product_margin(self, cr, uid, ids, field_name, arg, context=None):
        cur_obj = self.pool.get("res.currency")
        res = dict.fromkeys(ids)
        for line in self.browse(cr, uid, ids, context=context):
            quotation = line.quotation_id
            if quotation:
                res[line.id] = line.price_subtotal - quotation.price_subtotal
            else:
                cur = line.order_id.pricelist_id.currency_id
                res[line.id] = 0
                if line.product_id:
                    tmp_margin = line.price_subtotal - ((line.purchase_price or line.product_id.standard_price) * line.product_uos_qty)
                    res[line.id] = cur_obj.round(cr, uid, cur, tmp_margin)
        return res

    def _quotation_price(self, cr, uid, ids, field_name, arg, context=None):
        res = dict.fromkeys(ids)
        for line in self.browse(cr, uid, ids, context):
            quotation = line.quotation_id
            if quotation:
                res[line.id] = quotation.price_subtotal
            else:
                res[line.id] = line.purchase_price
        return res

    _inherit = 'sale.order.line'
    _columns = {
        "quotation_price" : fields.function(_quotation_price, type="float", string="Cost Price", copy=False, readonly=True),
        "quotation_id" : fields.function(_quotation_id, type="many2one", obj="purchase.order.line", string="Quotation", copy=False, readonly=True),
        "quotation_ids" : fields.one2many("purchase.order.line", "sale_line_id", "Quotations", copy=False, readonly=True, states={'draft': [('readonly', False)]} ),
        "quotation_active" : fields.boolean("Quotation Active", copy=False),
        "quotation_all" : fields.function(_quotation_all, type="boolean", string="All Quotation Sent to Suppliers"),
        "margin": fields.function(_product_margin, string="Margin", digits_compute= dp.get_precision("Product Price"), store=True)
    }
