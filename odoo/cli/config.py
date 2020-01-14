# -*- coding: utf-8 -*-
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


import logging
import argparse
import os
import re
import threading
import time
import unittest
import locale

import odoo
import glob
import shutil
import fnmatch

from odoo import tools
from odoo.tools import misc
from odoo.modules.registry import Registry
from odoo.tools.config import config
from odoo.tools.translate import PoFileReader
from . import Command

from odoo.modules.module import get_test_modules
from odoo.modules.module import OdooTestRunner
from odoo.modules.module import unwrap_suite


_logger = logging.getLogger('config')


ADDON_META = "__manifest__.py"
ADDON_API = 13


class ConfigCommand(Command):
    """ Basic config command """
    
    def __init__(self):
        defaultLang = locale.getdefaultlocale()[0]        
        if defaultLang.startswith("de_"):
          defaultLang = "de_DE"
      
        self.parser = argparse.ArgumentParser(description="Odoo Config")
        self.parser.add_argument("--addons-path", metavar="ADDONS",                                 
                                 help="colon-separated list of paths to addons")
                        
        self.parser.add_argument("-d","--database", metavar="DATABASE",
                                 help="the database to modify")
            
        self.parser.add_argument("-m", "--module", metavar="MODULE", required=False)
        
        self.parser.add_argument("--pg_path", metavar="PG_PATH", help="specify the pg executable path")    
        self.parser.add_argument("--db_host", metavar="DB_HOST", default=False,
                             help="specify the database host")
        self.parser.add_argument("--db_password", metavar="DB_PASSWORD", default=False,
                             help="specify the database password")
        self.parser.add_argument("--db_port", metavar="DB_PORT", default=False,
                             help="specify the database port", type=int)
        self.parser.add_argument("--db_user", metavar="DB_USER", default=False,
                            help="specify the database user")
        self.parser.add_argument("--config", metavar="CONFIG", default=False,
                            help="specify the configuration")
        
        self.parser.add_argument("--debug", action="store_true")
        
        self.parser.add_argument("--lang", required=False, 
                                 help="Language (Default is %s)" % defaultLang, 
                                 default=defaultLang)
        
        self.parser.add_argument("--reinit", metavar="REINIT", default=False,
                            help="(Re)Init Views no or full")
        
        
    def run(self, args):  
        params = self.parser.parse_args(args)
        
        config_args = []
        
        if params.database:
            config_args.append("--database")
            config_args.append(params.database)
            
        if params.module:
            config_args.append("--module")
            config_args.append(params.module)
            
        if params.pg_path:
            config_args.append("--pg_path")
            config_args.append(params.pg_path)
            
        if params.db_host:
            config_args.append("--db_host")
            config_args.append(params.db_host)
            
        if params.db_password:
            config_args.append("--db_password")
            config_args.append(params.db_password)
            
        if params.db_port:
            config_args.append("--db_port")
            config_args.append(params.db_port)
            
        if params.db_user:
            config_args.append("--db_user")
            config_args.append(params.db_user)
            
        if params.addons_path:
            config_args.append("--addons-path")
            config_args.append(params.addons_path)
            
        if params.lang:
            config_args.append("--lang")
            config_args.append(params.lang)
            
        if params.config:
            config_args.append("--config")
            config_args.append(params.config)
            
        config.parse_config(config_args)
        
        if params.reinit:
            config["reinit"] = params.reinit  
        
        self.params = params
        self.run_config()
        
    def run_config(self):
        _logger.info("Nothing to do!")
        
    def run_config_env(self, local_vars):
        _logger.info("Nothing to do!")
        
    def setup_env(self, fct=None):
        # setup pool   
        with odoo.api.Environment.manage():
            if self.params.database:
                registry = odoo.registry(self.params.database)
                with registry.cursor() as cr:
                    uid = odoo.SUPERUSER_ID
                    ctx = odoo.api.Environment(cr, uid, {})['res.users'].context_get()
                    env = odoo.api.Environment(cr, uid, ctx)
                    try:
                      if fct:
                        fct(env)
                      else:
                        self.run_config_env(env)
                      
                    except  Exception as e:
                      if self.params.debug:
                        _logger.exception(e)
                      else:
                        _logger.error(e)

                    finally:
                      cr.rollback()
            else:
                self.run_config() 
        

class Update(ConfigCommand):
    """ Update Module/All """

    def run_config(self):
        if self.params.module:
            config["update"][self.params.module]=1
        else:
            config["update"]["all"]=1
            
        registry = Registry.new(self.params.database, update_module=True)
                
        # refresh
        try:
          if config["reinit"] == "full":
            with registry.cursor() as cr:
              cr.execute("select matviewname from pg_matviews")
              for (matview,) in cr.fetchall():
                _logger.info("refresh MATERIALIZED VIEW %s ..." % matview)
                cr.execute("REFRESH MATERIALIZED VIEW %s" % matview)
              cr.commit()
              _logger.info("finished refreshing views")
        except KeyError:
          pass
        
        
class Po_Export(ConfigCommand):
    """ Export *.po File """
    
    def run_config(self):
        # check module
        if not self.params.module:
            _logger.error("No module defined for export!")
            return
        # check path
        self.modpath = odoo.modules.get_module_path(self.params.module)
        if not self.modpath:
            _logger.error("No module %s not found in path!" % self.params.module)
            return
       
        # check lang
        self.lang = self.params.lang
        self.langfile = self.lang.split("_")[0] + ".po"
        self.langdir = os.path.join(self.modpath,"i18n")
        if not os.path.exists(self.langdir):
            _logger.warning("Created language directory %s" % self.langdir)
            os.mkdir(self.langdir)
        
        # run with env
        self.setup_env()
      
    def run_config_env(self, env):
        # check module installed
        if not self.env["ir.module.module"].search([("state","=","installed"),("name","=",self.params.module)]) :
            _logger.error("No module %s installed!" % self.params.module)
            return 
        
        export_filename = os.path.join(self.langdir, self.langfile)
        export_f = open(export_filename,"w")
        try:
            ignore = None
            ignore_filename = "%s.ignore" % export_filename
            if os.path.exists(ignore_filename):
              _logger.info("Load ignore file %s" % ignore_filename)
              ignore=set()
              fileobj = misc.file_open(ignore_filename)
              reader = PoFileReader(fileobj)
              for row in reader:
                if not row[4]:
                  ignore.add(row)
            
            _logger.info('Writing %s', export_filename)
            tools.trans_export(self.lang, [self.params.module], export_f, "po", env.cr, ignore=ignore)
        finally:
            export_f.close()

        
class Po_Import(Po_Export):
    """ Import *.po File """
    
    def __init__(self):
        super(Po_Import, self).__init__()
        self.parser.add_argument("--overwrite", action="store_true", default=True, help="Override existing translations")
    
    def run_config_env(self, env):
        # check module installed
        if not self.env["ir.module.module"].search([("state","=","installed"),("name","=",self.params.module)]):
            _logger.error("No module %s installed!" % self.params.module)
            return 
        
        import_filename = os.path.join(self.langdir, self.langfile)
        if not os.path.exists(import_filename):
            _logger.error("File %s does not exist!" % import_filename)
            return 
        
        # import 
        context = {'overwrite': self.params.overwrite }
        if self.params.overwrite:
            _logger.info("Overwrite existing translations for %s/%s", self.params.module, self.lang)
            
        cr = env.cr
        odoo.tools.trans_load(cr, import_filename, self.lang, module_name=self.params.module, context=context)
        cr.commit()  


class Po_Cleanup(Po_Export):
    """ Import *.po File """
    
    def __init__(self):
        super(Po_Cleanup, self).__init__()
    
    def run_config_env(self, env):
        # check module installed
        if not self.env["ir.module.module"].search([("state","=","installed"),("name","=",self.params.module)]):
            _logger.error("No module %s installed!" % self.params.module)
            return 
        
        import_filename = os.path.join(self.langdir, self.langfile)
        if not os.path.exists(import_filename):
            _logger.error("File %s does not exist!" % import_filename)
            return 
        
        cr = env.cr
        with open(import_filename) as f:
          tf = PoFileReader(f)
          for trans_type, name, res_id, source, trad, comments in tf:
            if not trad:
              _logger.info("DELETE %s,%s" % (source, self.lang))
              
              cr.execute("""DELETE FROM ir_translation WHERE src=%s 
                              AND lang=%s 
                              AND module IS NULL 
                              AND type='code' 
                              AND value IS NOT NULL""", (source, self.lang))
              
              cr.execute("""DELETE FROM ir_translation WHERE src=%s 
                              AND lang=%s 
                              AND module IS NULL 
                              AND value=%s""", (source, self.lang, source))
        cr.commit()
              
      
class Test(ConfigCommand):
    """ Import *.po File """
    
    def __init__(self):
      super(Test, self).__init__()
      self.parser.add_argument("--test-prefix", metavar="TEST_PREFIX", required=False, help="Specify the prefix of the method for filtering")
      self.parser.add_argument("--test-case", metavar="TEST_CASE", required=False, help="Specify the test case")
      self.parser.add_argument("--test-download", metavar="TEST_DOWNLOAD", required=False, help="Specify test download diretory (e.g. for reports)")
      self.parser.add_argument("--test-tags", metavar="TEST_TAGS", required=False, help="Specify test tags")
      self.parser.add_argument("--test-position", metavar="TEST_POSITION", required=False, help="Specify position tags: post_install, at_install")
    
    def run_config(self):
      config["testing"] = True
      if self.params.test_download:
        config["test_download"] = self.params.test_download
      # run with env
      self.setup_env()
    
    def run_test(self, module_name, test_prefix=None, test_case=None, test_tags=None, test_position=None):
      global current_test
      from odoo.tests.common import TagsSelector # Avoid import loop
      current_test = module_name
      
      def match_filter(test):
        if not test_prefix or not isinstance(test, unittest.TestCase):
          if not test_case:
            return True 
          return type(test).__name__ == test_case
        return test._testMethodName.startswith(test_prefix)
      
      mods = get_test_modules(module_name)
      threading.currentThread().testing = True
      config_tags = TagsSelector(test_tags)
      position_tag = TagsSelector(test_position)
      r = True
      for m in mods:
          tests = unwrap_suite(unittest.TestLoader().loadTestsFromModule(m))
          suite = unittest.TestSuite(t for t in tests if position_tag.check(t) and config_tags.check(t) and match_filter(t))
  
          if suite.countTestCases():
              t0 = time.time()
              t0_sql = odoo.sql_db.sql_counter
              _logger.info('%s running tests.', m.__name__)
              result = OdooTestRunner().run(suite)
              if time.time() - t0 > 5:
                  _logger.log(25, "%s tested in %.2fs, %s queries", m.__name__, time.time() - t0, odoo.sql_db.sql_counter - t0_sql)
              if not result.wasSuccessful():
                  r = False
                  _logger.error("Module %s: %d failures, %d errors", module_name, len(result.failures), len(result.errors))
  
      current_test = None
      threading.currentThread().testing = False
      return r
      
    def run_config_env(self, env):
      module_name = self.params.module
      test_prefix = self.params.test_prefix
      test_case = self.params.test_case
      test_tags = self.params.test_tags
      test_position = self.params.test_position
      cr = env.cr
      
      if self.params.module:
        modules = [self.params.module]
      else:
        cr.execute("SELECT name from ir_module_module WHERE state = 'installed' ")
        modules = [name for (name,) in cr.fetchall()]
        
      if modules:
        ok = True
        for module_name in modules:
          ok = self.run_test(module_name, test_prefix, test_case, test_tags, test_position) and ok
        if ok:
          _logger.info("Finished!")
        else:
          _logger.info("Failed!")
      else:
        _logger.warning("No Tests!")

        
class CleanUp(ConfigCommand):
    """ CleanUp Database """
    
    def __init__(self):
        super(CleanUp, self).__init__()
        self.parser.add_argument("--fix", action="store_true", help="Do/Fix all offered cleanup")
        self.parser.add_argument("--full", action="store_true", help="Intensive cleanup")
        self.parser.add_argument("--full-delete", dest="full_delete_modules", help="Delete Modules with all data")
        self.parser.add_argument("--delete", dest="delete_modules", help="Delete Modules only (data will be held)")
        self.parser.add_argument("--delete-lower", action="store_true", help="Delete Lower Translation")
        self.parser.add_argument("--delete-higher", action="store_true", help="Delete Higher Translation")
        self.parser.add_argument("--only-models", action="store_true", help="Delete unused Models")
        self.clean=True
    
    def fixable(self, msg):
        self.clean=False
        if self.params.fix:
            _logger.info("[FIX] %s" % msg)
        else:
            _logger.warning("[FIXABLE] %s" % msg)
    
    def notfixable(self, msg):
        self.clean=False
        _logger.warning("[MANUAL FIX] %s" % msg)
    
    
    def cleanup_translation(self, env):
        cr = env.cr
        cr.execute("SELECT id, lang, name, res_id, module FROM ir_translation WHERE type='model' ORDER BY lang, module, name, res_id, id")
        refs = {}
        
        for row in self.cr.fetchall():
            # get name an res id
            name = row[2] and row[2].split(",")[0] or None
            res_id = row[3]            
            if name and res_id:
                ref = (name, res_id)
                ref_valid = False
                
                if ref in refs:
                    ref_valid = refs.get(ref)
                else:
                    model_obj = env.get(name)
                    
                    # ignore uninstalled modules
                    if not model_obj or not model_obj._table:
                        continue
                    
                    cr.execute("SELECT COUNT(id) FROM %s WHERE id=%s" % (model_obj._table, res_id))
                    if self.cr.fetchone()[0]:
                        ref_valid = True
                        
                    refs[ref] = ref_valid
                    
                # check if it is to delete
                if not ref_valid:
                    self.fixable("Translation object %s,%s no exist" % (name, res_id))
                    cr.execute("DELETE FROM ir_translation WHERE id=%s", (row[0],))
            
    
    def cleanup_double_translation(self, cr):
        # check model translations
        cr.execute("SELECT id, lang, name, res_id, module FROM ir_translation WHERE type='model' ORDER BY lang, module, name, res_id, id")
        last_key = None
        first_id = None
        for row in self.cr.fetchall():
            key = row[1:]
            if last_key and key == last_key:                
                self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                cr.execute("DELETE FROM ir_translation WHERE id=%s", (row[0],))
            else:
                first_id = row[0]
            last_key = key
        
        # check view translations    
        cr.execute("SELECT id, lang, name, src, module FROM ir_translation WHERE type='view' AND res_id=0 ORDER BY lang, module, name, src, id")
        last_key = None
        first_id = None
        for row in self.cr.fetchall():
            key = row[1:]
            if last_key and key == last_key:                
                self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                cr.execute("DELETE FROM ir_translation WHERE id=%s", (row[0],))
            else:
                first_id = row[0]
            last_key = key
            
        # show manual fixable
        cr.execute("SELECT id, lang, name, res_id FROM ir_translation WHERE type='model' AND NOT name LIKE 'ir.model%' ORDER BY lang, name, res_id, id")
        last_key = None
        first_id = None
        for row in cr.fetchall():
            key = row[1:]
            if last_key and key == last_key:
                if self.params.delete_lower and first_id < row[0]:
                  self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                  cr.execute("DELETE FROM ir_translation WHERE id=%s", (first_id,))
                  first_id = row[0]
                elif self.params.delete_higher and first_id > row[0]:
                  self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                  cr.execute("DELETE FROM ir_translation WHERE id=%s", (first_id,))
                  first_id = row[0]
                else:
                  self.notfixable("Double Translation %s for ID %s" % (repr(row), first_id))
            else:
                first_id = row[0]
            last_key = key
            
            
    def delete_model(self, env, model):
        self.deleted_models[model.id]=model.model
        self.fixable("Delete model %s,%s" % (model.model, model.id))
        
        for constraint in env["ir.model.constraint"].search([("model","=",model.id)]):
            self.fixable("Delete model constraint %s,%s" % (constraint.name, constraint.id))
            constraint.unlink()
        
        for access in env["ir.model.access"].search([("model_id","=",model.id)]):
            self.fixable("Delete model access %s,%s" % (access.name, access.id))
            access.unlink()
        
        for rel in env["ir.model.relation"].search([("model","=",model.id)]):
            self.fixable("Delete model relation %s,%s" % (rel.name, rel.id))
            rel.unlink()
            
        for data in env["ir.model.data"].search([("model","=",model.model)]):
            self.fixable("Delete model data %s,%s" % (data.name,data.id))
            data.unlink()
        
        for field in env["ir.model.fields"].search([("model_id","=",model.id)]):
            self.fixable("Delete model field %s,%s" % (field.name, field.id))
            self.cr.execute("DELETE FROM ir_model_fields WHERE id=%s",(field.id,))
        
        cr = env.cr
        cr.execute("SELECT id, name, type FROM ir_translation WHERE type IN ('model','field','view') AND name LIKE '%s%%'" % model.model)
        for oid, name, t in cr.fetchall():
            self.fixable("Delete model translation {id:%s|name:%s|type:%s}" % (oid, name, t))
            cr.execute("DELETE FROM ir_translation WHERE id=%s", (oid,))
        
        cr.execute("DELETE FROM ir_model WHERE id=%s", (model.id,))
        
    def delete_model_data(self, model_data):        
        self.fixable("Delete model_data %s,%s,%s,%s" % (model_data.name, model_data.id, model_data.model, model_data.res_id))
        env = model_data._env
        model_obj = env.get(model_data.model)
        if model_obj and model_obj._name != "ir.model":
            self.fixable("Delete %s,%s" % (model_obj._name,model_data.res_id))
            model_obj.browse(model_data.res_id).unlink()
        model_data.unlink()
            
    def delete_module(self, module, full=False):
        env = module._env
        cr = env.cr
        self.deleted_modules[module.id]=module.name
        self.fixable("Delete module %s,%s" % (module.name,module.id))
        cr.execute("UPDATE ir_module_module SET state='uninstalled' WHERE id=%s", (module.id,))
        
        if full:
          for model_data in env["ir.model.data"].search([("module","=",module.name)]):            
              self.delete_model_data(model_data)
            
        cr.execute("DELETE FROM ir_module_module_dependency WHERE name=%s OR module_id=%s", (module.name, module.id))
        cr.execute("DELETE FROM ir_module_module WHERE id=%s", (module.id,))
        cr.execute("DELETE FROM ir_model_data WHERE model='ir.module.module' AND res_id=%s", (module.id,))
        
    def cleanup_model_data(self, env):
        cr = env.cr
        cr.execute("SELECT d.id, d.model, d.res_id, d.name FROM ir_model_data d "
                        " INNER JOIN ir_module_module m ON  m.name = d.module AND m.state='installed' "
                        " WHERE d.res_id > 0 ")
        
        for oid, model, res_id, name in cr.fetchall():
            model_obj = env[model]
            
            deletable = False
            if not model_obj:                
                deletable = True
            else:
                cr.execute("SELECT id FROM %s WHERE id=%s" % (model_obj._table, res_id))
                if not cr.fetchall():                   
                    deletable = True
                    
            if deletable:
                self.fixable("ir.model.data %s/%s (%s) not exist" %  (model, res_id, name))
                cr.execute("DELETE FROM ir_model_data WHERE id=%s" % oid)
        
    def cleanup_modules(self, env):

        def getSet(value):
          if not value:
            return set()
          return set(re.split("[,|; ]+", value))
      
        mod_full_delete_set = getSet(self.params.full_delete_modules)
        mod_delete_set = getSet(self.params.delete_modules)
        
        cr = env.cr
        
        for module in env["ir.module.module"].search([]):
            info = odoo.modules.module.load_information_from_description_file(module.name)
            if not info:             
              mod_full_delete = module.name in mod_full_delete_set
              mod_delete = module.name in mod_delete_set
              if mod_delete or mod_full_delete:
                self.delete_module(module, mod_full_delete)
              else:
                self.notfixable("Delete module %s dependencies and set uninstalled, but module is left in db" % module.name)
                cr.execute("UPDATE ir_module_module SET state='uninstalled' WHERE id=%s", (module.id,))
                cr.execute("DELETE FROM ir_module_module_dependency WHERE name=%s OR module_id=%s", (module.name, module.id))

        # check invalid module data
        cr.execute("SELECT id, res_id, name FROM ir_model_data WHERE model='ir.module.module' AND res_id > 0")
        for model_data_id, module_id, name in cr.fetchall():
            module_name = name[7:]
            cr.execute("SELECT id FROM ir_module_module WHERE id=%s",(module_id,))
            res = cr.fetchone()
            if not res:
                self.fixable("Module %s for module data %s not exist" % (module_name, model_data_id))
                cr.execute("DELETE FROM ir_model_data WHERE id=%s", (model_data_id,))

                
    def cleanup_models(self, env):
        for model in env["ir.model"].search([]):          
            if not env.get(model.model):
                self.delete_model(model)
        
    def run_config_env(self, env):
        self.deleted_modules = {}
        self.deleted_models = {}
        
        # check full cleanup
        if self.params.full or self.params.only_models:
            cr = env.cr
            cr.autocommit(False)
            try:
                
              if self.params.only_models:
                self.cleanup_models(env)
              else:              
                self.cleanup_models(env)
                self.cleanup_modules(env)
                self.cleanup_model_data(env)    
                self.cleanup_translation(env)            
                
              if self.params.fix:
                cr.commit()
                        
            except Exception as e:
                _logger.error(e)
                return
            finally:
                cr.rollback()
        
        if not self.params.only_models:
          # open database
          db = odoo.sql_db.db_connect(self.params.database)
          
          # basic cleanup's
          cr = db.cursor()
          cr.autocommit(False)
          try:         
              self.cleanup_double_translation(cr)            
              if self.params.fix:
                  cr.commit()
          except Exception as e:
              _logger.error(e)
              return
          finally:
              cr.rollback()
              cr.close()
              
          if self.clean:
              _logger.info("Everything is CLEAN!")
          else:
              _logger.warning("Cleanup necessary")


class Link(ConfigCommand):
    """ Link addons from workspace """
    
    def __init__(self):
        super(Link, self).__init__()
        self.parser.add_argument("--cleanup", action="store_true", help="Cleanup links")
        
    def run_config(self):
        
        version = '%s.0' % ADDON_API
        dirEnabledAddons = os.path.expanduser('~/.local/share/Odoo/addons/%s' % version)
        dirServer = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath( __file__ )),"../.."))
        dirWorkspace = os.path.abspath(os.path.join(dirServer,".."))
        
        ignoreAddons = []
        includeAddons = {
        #       "addon-path" : [
        #          "modulexy"
        #        ]
        }
        
        def getDirs(inDir):
            res = []
            for dirName in os.listdir(inDir):
                if not dirName.startswith("."):
                    if os.path.isdir(os.path.join(inDir,dirName)):
                        res.append(dirName)
        
            return res
        
        def listDir(inDir):
            res = []
            for item in os.listdir(inDir):
                if not item.startswith("."):
                    res.append(item)
            return res
        
        def getMaintainedModules():
            addonPattern = [
              os.path.join(dirWorkspace,"addons*")
            ]
        
            addons = []
            for curPattern in addonPattern:
                curDir = os.path.dirname(curPattern)
                for curAddonPackageDir in glob.glob(curPattern):
                    curPackagePath = os.path.join(curDir,curAddonPackageDir)
                    if os.path.isdir(curPackagePath):
                        for curAddon in listDir(curPackagePath):
                            enabledPath = os.path.join(dirEnabledAddons, curAddon)
                            if os.path.exists(enabledPath):
                                addons.append(curAddon)
        
            return addons
        
        def findFile(directory, pattern):
            for root, dirs, files in os.walk(directory):
                for basename in files:
                    if fnmatch.fnmatch(basename, pattern):
                        filename = os.path.join(root, basename)
                        yield filename
        
        
        def cleanupPython(directory):
            for fileName in findFile(directory,"*.pyc"):
                os.remove(fileName)
        
        def setup(onlyLinks=False):
            #create path if not exists
            if not os.path.exists(dirEnabledAddons):
                _logger.info("Create directory %s" % dirEnabledAddons)
                os.makedirs(dirEnabledAddons)
                
            os.chmod(dirEnabledAddons, 0o710)
        
            filesToCopy = [
            ]
        
            addonPattern = [
              dirWorkspace+"/addons*"
            ]
        
            merged = []
            updateFailed = []
            
            if not onlyLinks:
              _logger.info("Cleanup all *.pyc Files")
              cleanupPython(dirWorkspace)
        
            if not os.path.exists(dirEnabledAddons):
                _logger.info("Create directory %s" % str(dirEnabledAddons))
                os.makedirs(dirEnabledAddons)
        
            removedLinks = 0
            addedLinks = 0
        
            dir_processed = set()
            dir_removed = set()
            dir_added = set()
        
            _logger.info("Delete current Symbolic links and distributed files " + str(dirEnabledAddons) + " ...")
            for curLink in glob.glob(dirEnabledAddons+'/*'):
                curLinkPath = os.path.join(dirEnabledAddons,curLink)
                #log.info("Found Link " + str(curLinkPath))
                is_link = os.path.islink(curLinkPath)
                if is_link: #or os.path.isfile(curLinkPath):
                    #log.info("Remove link " + str(curLinkPath))
                    os.remove(curLinkPath)
                    if is_link:
                        removedLinks+=1
                        dir_removed.add(os.path.basename(curLink))
        
            _logger.info("Distribute Files " + str(dirEnabledAddons) + " ...")
            for fileToCopy in filesToCopy:
                fileToCopyPath = os.path.join(dirServer,fileToCopy)
                if os.path.exists(fileToCopyPath):
                    fileDestPath = os.path.join(dirEnabledAddons,os.path.basename(fileToCopyPath))
                    _logger.info("Copy File %s to %s " % (fileToCopyPath,fileDestPath))
                    shutil.copyfile(fileToCopyPath, fileDestPath)
        
            #link per addons basis
            for curPattern in addonPattern:
                for curAddonPackageDir in glob.glob(curPattern):
                    packageName = os.path.basename(curAddonPackageDir)
                    if not curAddonPackageDir in dir_processed:
                        dir_processed.add(curAddonPackageDir)
                        _logger.info("Process: " + curAddonPackageDir)
                        if os.path.isdir(curAddonPackageDir):
                            #get include list
                            addonIncludeList = includeAddons.get(packageName,None)
                            #process addons
                            for curAddon in listDir(curAddonPackageDir):
                                if not curAddon in ignoreAddons and (addonIncludeList is None or curAddon in addonIncludeList):
                                    curAddonPath = os.path.join(curAddonPackageDir, curAddon)
                                    curAddonPathMeta = os.path.join(curAddonPath, ADDON_META)
                                    if os.path.exists(curAddonPathMeta):
                                        addonMeta = None
                                        with open(curAddonPathMeta) as metaFp:
                                            addonMeta = eval(metaFp.read())
                                            
                                        # check api
                                        supported_api = addonMeta.get("api")
                                        if not supported_api or ADDON_API in supported_api:                                     
                                            dstPath = os.path.join(dirEnabledAddons, curAddon)                            
                                            if not os.path.exists(dstPath) and not curAddonPath.endswith(".pyc"):
                                                #log.info("Create addon link " + str(dstPath) + " from " + str(curAddonPath))
                                                os.symlink(curAddonPath, dstPath, target_is_directory=True)
                                                addedLinks += 1
                                                dir_added.add(curAddon)
        
                    else:
                        #log.info("processed twice: " + curAddonPackageDir)
                        pass
        
            for cur_dir in dir_removed:
                if not cur_dir in dir_added:
                    _logger.info("Removed Addin: " + cur_dir)
        
            for cur_dir in dir_added:
                if not cur_dir in dir_removed:
                    _logger.info("Addin Added: " + cur_dir)
        
            if merged:
                _logger.info("\n\nMerged:\n * %s\n" % ("\n * ".join(merged),))
        
            if updateFailed:
                _logger.error("\n\nUnable to update:\n * %s\n" % ("\n * ".join(updateFailed),))
        
            _logger.info("Removed links: " + str(removedLinks))
            _logger.info("Added links: "  + str(addedLinks))
            _logger.info("Finished!")
        
        
        setup(onlyLinks=not self.params.cleanup)
              
