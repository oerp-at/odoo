# -*- coding: utf-8 -*-
# -*- encoding: utf-8 -*-

#############################################################################
#
#    Copyright (c) 2007 Martin Reisenhofer <martin.reisenhofer@funkring.net>
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

from openerp.osv import fields, osv
from collections import defaultdict
   

class sale_order_line(osv.osv):    
    _inherit = "sale.order.line"
    
    def _commission_fields(self, cr, uid, ids, field_names, arg, context=None):
      res = dict.fromkeys(ids)
      commission_task_obj = self.pool["commission.task"]
      order_obj = self.pool["sale.order"]
      
      cr.execute("SELECT l.order_id FROM sale_order_line l WHERE l.id IN %s GROUP BY 1", (tuple(ids),))
      order_ids = [r[0] for r in cr.fetchall()]
    
      commission_dict = defaultdict(list)
      for order in order_obj.browse(cr, uid, order_ids, context=context):
        for commission_line in commission_task_obj._commission_sale(cr, uid, order, context=context):
          commission_dict[commission_line["order_line_id"]].append(commission_line)
      
      for line in self.browse(cr, uid, ids, context):
        
        commission_amount = 0.0
        commission = 0.0
        
        for c in commission_dict[line.id]:
          commission_amount += c["amount"]
          
        if line.price_subtotal:
          commission = 100 / line.price_subtotal * (commission_amount*-1.0)
        
        res[line.id] = {
          "commission_amount": commission_amount,
          "commission": commission
        }

      return res
    
    
    _columns = {
      "commission": fields.function(_commission_fields, type="float", string="Commission %", multi="_commission_fields", readonly=True),
      "commission_custom": fields.float("Custom Commission %", readonly=True, states={"draft": [("readonly", False)], "sent": [("readonly", False)]}),
      "commission_amount": fields.function(_commission_fields, type="float", string="Commission Amount", multi="_commission_fields", readonly=True)
    }
    
    
    def _product_margin_extra(self, cr, uid, line, context=None):
      res = super(sale_order_line, self)._product_margin_extra(cr, uid, line, context=context)
      res += line.commission_amount        
      return res
    

    
