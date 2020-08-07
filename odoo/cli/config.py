# -*- coding: utf-8 -*--
# © 2017 Funkring.net (Martin Reisenhofer <martin.reisenhofer@funkring.net>)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import argparse
import os
import sys
import re
import threading
import time
import unittest
import locale
import psycopg2
from multiprocessing import Pool

import odoo
import glob
import shutil
import fnmatch

from odoo.tools import misc
from odoo.modules.registry import Registry
from odoo.tools.config import config
from odoo.tools.translate import PoFileReader, PoFileWriter, trans_generate

from . import Command
from . server import main

from odoo.modules.module import get_test_modules
from odoo.modules.module import OdooTestRunner
from odoo.modules.module import unwrap_suite
from odoo.modules.module import MANIFEST_NAMES

_logger = logging.getLogger('config')

ADDON_API = odoo.release.version


def get_python_lib():
    version = sys.version.split(".")
    if len(version) >= 2:
        return "python%s.%s" % (version[0], version[1])
    elif len(version) == 1:
        return "python%s.%s" % version[0]
    return "python%s" % version

def required_or_default(name, h):
    """
    Helper to define `argparse` arguments. If the name is the environment,
    the argument is optional and draw its value from the environment if not
    supplied on the command-line. If it is not in the environment, make it
    a mandatory argument.
    """
    d = None
    if os.environ.get("ODOO" + name.upper()):
        d = {"default": os.environ["ODOO" + name.upper()]}
    else:
        # default addon path
        if name == "ADDONS":
            dir_server = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../.."))
            dir_workspace = os.path.abspath(os.path.join(dir_server, ".."))

            addon_pattern = [dir_workspace + "/addons*"]
            package_paths = set()
            for cur_pattern in addon_pattern:
                for package_dir in glob.glob(cur_pattern):                    
                    if os.path.isdir(package_dir):
                        package_paths.add(package_dir)

            # add package paths
            if package_paths:
                d = {"default": ",".join(package_paths)}
          
        if not d:
            d = {"required": True}

    d["help"] = h + ". The environment variable ODOO" + name.upper() + " can be used instead."
    return d


class ConfigCommand(Command):
    """ Basic config command """
    
    def __init__(self):
        defaultLang = locale.getdefaultlocale()[0]        
        if defaultLang.startswith("de_"):
          defaultLang = "de_DE"
      
        self.parser = argparse.ArgumentParser(description="Odoo Config")
        self.parser.add_argument(
            "--addons-path",
            metavar="ADDONS",
            **required_or_default("ADDONS", "Colon-separated list of paths to addons")
        )
                        
        self.parser.add_argument("-d","--database", metavar="DATABASE",
                                 help="The database to modify")
            
        self.parser.add_argument("-m", "--module", metavar="MODULE", required=False)
        
        self.parser.add_argument("--pg_path", metavar="PG_PATH", help="Specify the pg executable path")    
        self.parser.add_argument("--db_host", metavar="DB_HOST", default=False,
                             help="specify the database host")
        self.parser.add_argument("--db_password", metavar="DB_PASSWORD", default=False,
                             help="Specify the database password")
        self.parser.add_argument("--db_port", metavar="DB_PORT", default=False,
                             help="Specify the database port", type=int)
        self.parser.add_argument("--db_user", metavar="DB_USER", default=False,
                            help="Specify the database user")
        self.parser.add_argument("--db_prefix", metavar="DB_PREFIX", default=False, 
                            help="specify database prefix")
        self.parser.add_argument("--config", metavar="CONFIG", default=False,
                            help="Specify the configuration")
        self.parser.add_argument("--db-config", "-dc", metavar="DB_CONFIG", default=False, 
                            help="Specify database configuration")
        
        self.parser.add_argument("--debug", action="store_true")
        
        self.parser.add_argument("--lang", required=False, 
                                 help="Language (Default is %s)" % defaultLang, 
                                 default=defaultLang)
        
        self.parser.add_argument("--reinit", metavar="REINIT", default=False, help="(Re)init materialized views, yes for reinit or full for reinit and rebuild")

        self.parser.add_argument("--test-enable", action="store_true", help="Run tests")
        
        
    def run(self, args):  
        params = self.parser.parse_args(args)
        
        config_args = []

        default_mapping = {
            "db_name": "database",
            "db_host": "db_host",
            "db_password": "db_password",
            "db_port": "db_port",
            "db_user": "db_user",
            "db_prefix": "db_prefix"
        }

        if params.db_config:
            if os.path.exists(params.db_config):
                p = ConfigParser.ConfigParser()
                try:
                    p.read([params.db_config])
                    for (name, value) in p.items("options"):
                        param_name = default_mapping.get(name)
                        if value and param_name:
                            if value.lower() == "none":
                                value = None
                            if value.lower() == "false":
                                value = False
                            if name == "db_port":
                                value = int(value)

                            # set default
                            # if is not defined
                            if value:
                                if not getattr(params, param_name):
                                    setattr(params, param_name, value)
                except IOError:
                    _logger.error("Unable to read config %s" % params.db_config)
                except ConfigParser.NoSectionError:
                    _logger.error("Config %s has no section options" % params.db_config)
            else:
                _logger.error("Config %s not found" % params.db_config)

            
        if params.module:
            config_args.append("--module")
            config_args.append(params.module)
            
        if params.pg_path:
            config_args.append("--pg_path")
            config_args.append(params.pg_path)

        if params.database:
            config_args.append("--database")
            config_args.append(params.database)
        elif not params.db_prefix:
            raise NameError("No database specified tue parameter or config file!")
            
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
        
    def run_config_env(self, env):
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


def update_database(database):
    registry = Registry.new(database, update_module=True)
            
    # refresh
    try:
        if config["reinit"] == "full":
            with registry.cursor() as cr:
                cr.execute("SELECT matviewname FROM pg_matviews")
                
                for (matview,) in cr.fetchall():
                    _logger.info("REFRESH MATERIALIZED VIEW %s ..." % matview)
                    cr.execute("REFRESH MATERIALIZED VIEW %s" % matview)
                    cr.commit()                    

                _logger.info("Finished refreshing views")
    except KeyError:
        pass


class Update(ConfigCommand):
    """ Update Module/All """

    def __init__(self):
        super(Update, self).__init__()
        self.parser.add_argument(
            "--db-all", action="store_true", default=False, help="Update all databases which match the defined prefix"
        )
        self.parser.add_argument(
            "--threads", metavar="THREADS", default=32, help="Number of threads for multi database update"
        )

    def get_databases(self):
        # get databases
        params = ["dbname='postgres'"]
        def add_param(name, name2):
            value = config.get(name)
            if value:
                params.append("%s='%s'" % (name2, value))

        add_param("db_host","host")
        add_param("db_user","user")
        add_param("db_password","password")
        add_param("db_port","port")

        params = " ".join(params)
        con = psycopg2.connect(params)
        try:
            cr = con.cursor()
            try:                
                cr.execute("SELECT datname FROM pg_database WHERE datname LIKE '%s_%%'" % self.params.db_prefix)
                return [r[0] for r in cr.fetchall()]
            finally:
                cr.close()
        finally:
            con.close()

    def run_config(self):
        # set reinit to no 
        # if it was not provided     
        if not self.params.reinit:
            config["reinit"] = "no"

        if self.params.module:
            config["update"][self.params.module]=1
        else:
            config["update"]["all"]=1
            
        if self.params.db_all:

            if not self.params.db_prefix:
                _logger.error("For multi database update you need to specify the --db_prefix parameter")
                return

            _logger.info("Create thread pool (%s) for update" % self.params.threads)

            pool = Pool(processes=self.params.threads)
            pool.map(update_database, self.get_databases())

        else:
            update_database(self.params.database)
        

class PoIgnoreFileWriter(PoFileWriter):
  def __init__(self, target, modules, lang, ignore):
    super(PoIgnoreFileWriter, self).__init__(target, modules, lang)
    self.ignore = ignore
    
  def write_rows(self, rows):
    # we now group the translations by source. That means one translation per source.
    grouped_rows = {}
    for module, type, name, res_id, src, trad, comments in rows:
        row = grouped_rows.setdefault(src, {})
        row.setdefault('modules', set()).add(module)
        if not row.get('translation') and trad != src:
            row['translation'] = trad
        row.setdefault('tnrs', []).append((type, name, res_id))
        row.setdefault('comments', set()).update(comments)

    for src, row in sorted(grouped_rows.items()):
        if not self.lang:
            # translation template, so no translation value
            row['translation'] = ''
        elif not row.get('translation'):
            row['translation'] = ''
        
        # check if translations should ignored
        write_translation = True                
        if self.ignore:
          for tnr in row["tnrs"]:
            comments = row['comments']
            if not comments:
              comments = ['']
            for comment in comments:
              # type, name, imd_name, src, value, comments
              key = (tnr[0], tnr[1], str(tnr[2]), src, row['translation'], comment)
              if key in self.ignore:
                write_translation = False    
        
        if write_translation:
          self.add_entry(row['modules'], row['tnrs'], src, row['translation'], row['comments'])
          
    # buffer expects bytes
    self.buffer.write(str(self.po).encode())
        
             
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
      
    def trans_export(self, lang, modules, buffer, cr, ignore):
      translations = trans_generate(lang, modules, cr)
      modules = set(t[0] for t in translations)
      writer = PoIgnoreFileWriter(buffer, modules, lang, ignore)
      writer.write_rows(translations)
      del translations
      
    def run_config_env(self, env):
        # check module installed
        if not env["ir.module.module"].search([("state","=","installed"),("name","=",self.params.module)]) :
            _logger.error("No module %s installed!" % self.params.module)
            return 
        
        exportFileName = os.path.join(self.langdir, self.langfile)
        with open(exportFileName,"wb") as exportStream:
            ignore = None
            ignore_filename = "%s.ignore" % exportFileName
            if os.path.exists(ignore_filename):
              _logger.info("Load ignore file %s" % ignore_filename)
              ignore=set()
              with misc.file_open(ignore_filename, mode="rb") as fileobj:
                reader = PoFileReader(fileobj)
                for row in reader:
                  if not row.get("value"):
                    # type, name, imd_name, src, value, comments
                    imd_name = row.get("imd_name")
                    module = row.get("module") or ""
                    if imd_name and module and not imd_name.find(".") > 0:
                      imd_name = "%s.%s" % (module, imd_name)                    
                    ignore.add((row["type"], 
                                row["name"],
                                imd_name,
                                row["src"],
                                row["value"],
                                row["comments"]))
            
            _logger.info('Writing %s', exportFileName)
            self.trans_export(self.lang, [self.params.module], exportStream, env.cr, ignore)

        
class Po_Import(Po_Export):
    """ Import *.po File """
    
    def __init__(self):
        super(Po_Import, self).__init__()
        self.parser.add_argument("--overwrite", action="store_true", default=True, help="Override existing translations")
    
    def run_config_env(self, env):
        # check module installed
        if not env["ir.module.module"].search([("state","=","installed"),("name","=",self.params.module)]):
            _logger.error("No module %s installed!" % self.params.module)
            return 
        
        importFilename = os.path.join(self.langdir, self.langfile)
        if not os.path.exists(importFilename):
            _logger.error("File %s does not exist!" % importFilename)
            return 
        
        # import 
        context = {'overwrite': self.params.overwrite }
        if self.params.overwrite:
            _logger.info("Overwrite existing translations for %s/%s", self.params.module, self.lang)
            
        cr = env.cr
        odoo.tools.trans_load(cr, importFilename, self.lang, module_name=self.params.module, context=context)
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
      config_tags = TagsSelector(test_tags) if test_tags else None
      position_tag = TagsSelector(test_position) if test_position else None
      r = True
      for m in mods:
          tests = unwrap_suite(unittest.TestLoader().loadTestsFromModule(m))
          suite = unittest.TestSuite(t for t in tests if (not position_tag or position_tag.check(t)) and (not config_tags or config_tags.check(t)) and match_filter(t))
  
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
        self.ignore_modules = set(('timesheet_grid',
                             'stock_barcode',
                             'account_accountant',
                             'mrp_workorder',
                             'mrp_plm',
                             'quality_control',
                             'web_studio',
                             'helpdesk',
                             'hr_appraisal',
                             'payment_sepa_direct_debit',
                             'project_forecast',
                             'sale_ebay',
                             'sale_subscription',
                             'sign',
                             'voip',
                             'website_calendar',
                             'website_twitter_wall',
                             'marketing_automation',
                             'web_mobile'))
        
    def run_config(self):
        # run with env
        self.setup_env()
    
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
        
        for row in cr.fetchall():
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
        for row in cr.fetchall():
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
        for row in cr.fetchall():
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
        model_obj = env.get(model_data.model, None)
        if not model_obj is None and model_obj._name != "ir.model":
            self.fixable("Delete %s,%s" % (model_obj._name,model_data.res_id))
            model_obj.browse(model_data.res_id).unlink()
        model_data.unlink()
            
    def delete_module(self, module, full=False):
        env = module.env
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
            model_obj = env.get(model,None)
            
            deletable = False
            if model_obj is None:                
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
            if not info and module.name not in self.ignore_modules:             
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
            model_obj = env.get(model.model, None)
            if model_obj is None:
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
                if self.params.debug:
                  _logger.exception(e)
                else:
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
              if self.params.debug:
                _logger.exception(e)
              else:
                _logger.error(e)
              return
          finally:
              cr.rollback()
              cr.close()
              
          if self.clean:
              _logger.info("Everything is CLEAN!")
          else:
              _logger.warning("Cleanup necessary")


###############################################################################
# Setup Utils
###############################################################################


def getDirs(inDir):
    res = []
    for dirName in os.listdir(inDir):
        if not dirName.startswith("."):
            if os.path.isdir(os.path.join(inDir, dirName)):
                res.append(dirName)

    return res


def listDir(inDir):
    res = []
    for item in os.listdir(inDir):
        if not item.startswith("."):
            res.append(item)
    return res


def findFile(directory, pattern):
    for root, dirs, files in os.walk(directory):
        for basename in files:
            if fnmatch.fnmatch(basename, pattern):
                filename = os.path.join(root, basename)
                yield filename


def cleanupPython(directory):
    for fileName in findFile(directory, "*.pyc"):
        os.remove(fileName)


def linkFile(src, dst):
    if os.path.exists(dst):
        if os.path.islink(dst):
            os.remove(dst)
    os.symlink(src, dst)


def linkDirectoryEntries(src, dst, ignore=None, names=None):
    links = set()

    # remove old links
    for name in listDir(dst):
        if ignore and name in ignore:
            continue
        if names and not name in names:
            continue
        file_path = os.path.join(dst, name)
        if os.path.islink(file_path):
            os.remove(file_path)

    # set new links
    for name in listDir(src):
        if ignore and name in ignore:
            continue
        if names and not name in names:
            continue
        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)
        is_dir = os.path.isdir(dst_path)
        if not name.endswith(".pyc") and not name.startswith("."):
            os.symlink(src_path, dst_path)
            links.add(dst_path)

    return links


class Install(Command):
    """ install to environment """

    def __init__(self):
        super(Install, self).__init__()
        self.parser = argparse.ArgumentParser(description="Odoo Config")
        self.parser.add_argument("--cleanup", action="store_true", help="Cleanup links")

    def run(self, args):
        params = self.parser.parse_args(args)

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

        virtual_env = os.environ.get("VIRTUAL_ENV")
        if not virtual_env:
            _logger.error("Can only executed from virtual environment")
            return

        lib_path = os.path.join(virtual_env,"lib",get_python_lib())
        lib_path_odoo = os.path.join(lib_path, "odoo")
        lib_path_addons = os.path.join(lib_path_odoo, "addons")
        bin_path = os.path.join(virtual_env, "bin")

        # create directories
        for dir_path in (lib_path_odoo, lib_path_addons):
            if not os.path.exists(dir_path):
                _logger.info("Create directory %s" % dir_path)
                os.mkdir(dir_path)

        dirServer = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../.."))
        dirWorkspace = os.path.abspath(os.path.join(dirServer, ".."))
        dirEnabledAddons = lib_path_addons

        ignoreAddons = []
        includeAddons = {
            #       "addon-path" : [
            #          "modulexy"
            #        ]
        }

        def getAddonsSet():
            addons = set()
            for name in getDirs(dirEnabledAddons):
                addons.add(name)
            return addons

        def setupAddons(onlyLinks=False):
            dir_odoo = os.path.join(dirServer, "odoo")
            dir_odoo_addons = os.path.join(dir_odoo, "addons")
            old_addons = getAddonsSet()

            # setup odoo libs

            linkDirectoryEntries(dir_odoo, lib_path_odoo, ignore="addons")
            linkedBaseEntries = linkDirectoryEntries(dir_odoo_addons, lib_path_addons)

            # setup odoo bin

            odoo_bin = os.path.join(dirServer, "odoo-bin")
            linkFile(odoo_bin, os.path.join(bin_path, "odoo-bin"))

            # setup addons

            addonPattern = [dirWorkspace + "/addons*", os.path.join(dirServer, "addons")]

            merged = []
            updateFailed = []

            if not onlyLinks:
                _logger.info("Cleanup all *.pyc Files")
                cleanupPython(dirWorkspace)

            if not os.path.exists(dirEnabledAddons):
                _logger.info("Create directory %s" % str(dirEnabledAddons))
                os.makedirs(dirEnabledAddons)

            dir_processed = set()

            _logger.info("Delete current Symbolic links and distributed files " + str(dirEnabledAddons) + " ...")
            for curLink in glob.glob(dirEnabledAddons + "/*"):
                curLinkPath = os.path.join(dirEnabledAddons, curLink)
                is_link = os.path.islink(curLinkPath)
                if is_link:
                    # ingore system link
                    if curLinkPath in linkedBaseEntries:
                        continue
                    # remove link
                    os.remove(curLinkPath)

            # link per addons basis
            for curPattern in addonPattern:
                for curAddonPackageDir in glob.glob(curPattern):
                    packageName = os.path.basename(curAddonPackageDir)
                    if not curAddonPackageDir in dir_processed:
                        dir_processed.add(curAddonPackageDir)
                        _logger.info("Process: " + curAddonPackageDir)
                        if os.path.isdir(curAddonPackageDir):
                            # get include list
                            addonIncludeList = includeAddons.get(packageName, None)
                            # process addons
                            for curAddon in listDir(curAddonPackageDir):
                                if not curAddon in ignoreAddons and (
                                    addonIncludeList is None or curAddon in addonIncludeList
                                ):
                                    curAddonPath = os.path.join(curAddonPackageDir, curAddon)
                                    for manifest_name in MANIFEST_NAMES:
                                        curAddonPathMeta = os.path.join(curAddonPath, manifest_name)
                                        if os.path.exists(curAddonPathMeta):
                                            addonMeta = None
                                            with open(curAddonPathMeta) as metaFp:
                                                addonMeta = eval(metaFp.read())

                                            # check api
                                            supported_api = addonMeta.get("api")
                                            if not supported_api or ADDON_API in supported_api:
                                                dstPath = os.path.join(dirEnabledAddons, curAddon)
                                                if not os.path.exists(dstPath) and not curAddonPath.endswith(".pyc"):
                                                    # log.info("Create addon link " + str(dstPath) + " from " + str(curAddonPath))
                                                    os.symlink(curAddonPath, dstPath)

                    else:
                        # log.info("processed twice: " + curAddonPackageDir)
                        pass

            installed_addons = getAddonsSet()
            addons_removed = old_addons - installed_addons
            addons_added = installed_addons - old_addons
            
            _logger.info("Addon API: %s" % ADDON_API)

            for addon in addons_removed:
                _logger.info("Removed: %s" % addon)

            for addon in addons_added:
                _logger.info("Added: %s" % addon)

            if merged:
                _logger.info("\n\nMerged:\n * %s\n" % ("\n * ".join(merged),))

            if updateFailed:
                _logger.error("\n\nUnable to update:\n * %s\n" % ("\n * ".join(updateFailed),))

            _logger.info("Removed links: %s" % len(addons_removed))
            _logger.info("Added links: %s" % len(addons_added))
            _logger.info("Finished!")

        setupAddons(onlyLinks=not params.cleanup)


###############################################################################
# Serve
###############################################################################

class Serve(Command):
    """Quick start the Odoo server for your project"""

    def get_module_list(self, path):
        mods = glob.glob(os.path.join(path, "*/%s" % MANIFEST))
        return [mod.split(os.path.sep)[-2] for mod in mods]
    
    def run(self, cmdargs):
        parser = argparse.ArgumentParser(prog="%s start" % sys.argv[0].split(os.path.sep)[-1], description=self.__doc__)

        parser.add_argument("--create", action="store_true", help="Create databse if it not exist")
        parser.add_argument(
            "--path", help="Directory where your project's modules are stored (will autodetect from current dir)"
        )
        parser.add_argument(
            "-d",
            "--database",
            dest="db_name",
            default=None,
            help="Specify the database name (default to project's directory name",
        )

        parser.add_argument("--debug", action="store_true")

        args, unknown = parser.parse_known_args(args=cmdargs)

        dir_server = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../.."))
        dir_workspace = os.path.abspath(os.path.join(dir_server, ".."))

        if args.path:
            dir_workspace = os.path.abspath(os.path.expanduser(os.path.expandvars(args.path)))

        # get addons paths
        if "--addons-path" not in cmdargs:
            addon_pattern = [dir_server + "/addons",
                             dir_workspace + "/addons*"]
            package_paths = set()
            for cur_pattern in addon_pattern:
                for package_dir in glob.glob(cur_pattern):
                    package_name = os.path.basename(package_dir)
                    if os.path.isdir(package_dir):
                        package_paths.add(package_dir)

            # add package paths
            if package_paths:
                cmdargs.append("--addons-path=%s" % ",".join(package_paths))

        if args.db_name or args.create:
            if not args.db_name:
                args.db_name = db_name or project_path.split(os.path.sep)[-1]
                cmdargs.extend(("-d", args.db_name))

            # TODO: forbid some database names ? eg template1, ...
            if args.create:
                try:
                    _create_empty_database(args.db_name)
                except DatabaseExists as e:
                    pass
                except Exception as e:
                    die("Could not create database `%s`. (%s)" % (args.db_name, e))

            if "--db-filter" not in cmdargs:
                cmdargs.append("--db-filter=^%s$" % args.db_name)

        # remove package paths, to allow debugger find
        # local modul source code
        if args.debug:
            odoo.addons.__path__ =  [odoo.addons.__path__[0]]

        # Remove --path /-p options from the command arguments
        def to_remove(i, l):
            return l[i] == "--debug" or l[i] == "-p" or l[i].startswith("--path") or (i > 0 and l[i - 1] in ["-p", "--path"])

        cmdargs = [v for i, v in enumerate(cmdargs) if not to_remove(i, cmdargs)]        
        main(cmdargs)


def die(message, code=1):
    print >>sys.stderr, message
    sys.exit(code)
              
