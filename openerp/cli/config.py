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
import openerp
import argparse
import os
import re
import inspect
import threading
import time
import unittest
import unittest2
import itertools
import glob
import sys
import fnmatch
import psycopg2

from multiprocessing import Pool

import ConfigParser

from openerp import tools
from openerp.tools import misc
from openerp import api
from openerp.tools.config import config
from openerp.tools.translate import TinyPoFile
from openerp.modules.registry import RegistryManager

from . import Command
from . server import main

from openerp.modules.module import MANIFEST
from openerp.service.db import _create_empty_database, DatabaseExists

from openerp.modules.module import get_test_modules
from openerp.modules.module import TestStream
from openerp import SUPERUSER_ID

ADDON_API = openerp.release.version
_logger = logging.getLogger("openerp")


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
    """ Basic Config Command """

    def __init__(self):
        self.parser = argparse.ArgumentParser(description="Odoo Config")
        self.parser.add_argument(
            "--addons-path",
            metavar="ADDONS",
            **required_or_default("ADDONS", "colon-separated list of paths to addons")
        )

        self.parser.add_argument("-d", "--database", metavar="DATABASE")

        self.parser.add_argument("-m", "--module", metavar="MODULE", required=False)
        self.parser.add_argument("--default-lang", required=False)

        self.parser.add_argument("--pg_path", metavar="PG_PATH", help="specify the pg executable path")
        self.parser.add_argument("--db_host", metavar="DB_HOST", default=False, help="specify the database host")
        self.parser.add_argument(
            "--db_password", metavar="DB_PASSWORD", default=False, help="specify the database password"
        )
        self.parser.add_argument(
            "--db_port", metavar="DB_PORT", default=False, help="specify the database port", type=int
        )
        self.parser.add_argument("--db_user", metavar="DB_USER", default=False, help="specify the database user")
        self.parser.add_argument("--db_prefix", metavar="DB_PREFIX", default=False, help="specify database prefix")
        self.parser.add_argument("--config", metavar="CONFIG", default=False, help="specify the configuration")

        self.parser.add_argument(
            "--db-config", "-dc", metavar="DB_CONFIG", default=False, help="specify database configuration"
        )

        self.parser.add_argument("--debug", action="store_true")

        self.parser.add_argument("--lang", required=False, help="language (Default is %s)" % config.defaultLang)

        self.parser.add_argument("--reinit", metavar="REINIT", default=False, help="(re)init materialized views, yes for reinit or full for reinit and rebuild")

        self.parser.add_argument("--test-enable", action="store_true", help="run tests")

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

        if params.default_lang:
            config_args.append("--default-lang")
            config_args.append(params.default_lang)

        if params.pg_path:
            config_args.append("--pg_path")
            config_args.append(params.pg_path)

        if params.database:
            config_args.append("--database")
            config_args.append(params.database)
        elif not params.db_prefix:
            raise NameError("No database specified with parameter or config file!")

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

        if params.test_enable:
            config_args.append("--test-enable")

        config.parse_config(config_args)

        if params.reinit:
            config["reinit"] = params.reinit
        
        self.params = params
        self.run_config()

    def run_config(self):
        _logger.info("Nothing to do!")

    def run_config_env(self):
        _logger.info("Nothing to do!")

    def setup_env(self, fct=None):
        # setup pool
        self.pool = RegistryManager.get(self.params.database)
        self.cr = self.pool.cursor()
        try:
            # create environment
            with api.Environment.manage():
                self.env = openerp.api.Environment(self.cr, 1, {})
                if fct:
                    fct(self.env)
                else:
                    self.run_config_env()

            self.cr.commit()
        except Exception, e:
            if self.params.debug:
                _logger.exception(e)
            else:
                _logger.error(e)
        finally:
            self.cr.rollback()
            self.cr.close()



def update_database(database):
    registry = RegistryManager.get(database, update_module=True)
    # refresh
    try:
        if config["reinit"] == "full":
            cr = registry.cursor()
            try:
                cr.execute("select matviewname from pg_matviews")
                for (matview,) in cr.fetchall():
                    _logger.info("refresh MATERIALIZED VIEW %s ..." % matview)
                    cr.execute("REFRESH MATERIALIZED VIEW %s" % matview)
                cr.commit()
                _logger.info("finished refreshing views")
            finally:
                cr.close()

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
            config["update"][self.params.module] = 1
        else:
            config["update"]["all"] = 1
                
        if self.params.db_all:

            if not self.params.db_prefix:
                _logger.error("For multi database update you need to specify the --db_prefix parameter")
                return

            _logger.info("Create thread pool (%s) for update" % self.params.threads)

            pool = Pool(processes=self.params.threads)
            pool.map(update_database, self.get_databases())
            pool.close()
            pool.join_thread()
        else:
            update_database(self.params.database)
            

class Po_Export(ConfigCommand):
    """ Export *.po File """

    def run_config(self):
        # check module
        if not self.params.module:
            _logger.error("No module defined for export!")
            return
        # check path
        self.modpath = openerp.modules.get_module_path(self.params.module)
        if not self.modpath:
            _logger.error("No module %s not found in path!" % self.params.module)
            return

        # check lang
        self.lang = self.params.lang or config.defaultLang
        self.langfile = self.lang.split("_")[0] + ".po"
        self.langdir = os.path.join(self.modpath, "i18n")
        if not os.path.exists(self.langdir):
            _logger.warning("Created language directory %s" % self.langdir)
            os.mkdir(self.langdir)

        # run with env
        self.setup_env()

    def run_config_env(self):
        # check module installed
        self.model_obj = self.pool["ir.module.module"]
        if not self.model_obj.search_id(self.cr, 1, [("state", "=", "installed"), ("name", "=", self.params.module)]):
            _logger.error("No module %s installed!" % self.params.module)
            return

        export_filename = os.path.join(self.langdir, self.langfile)
        export_f = file(export_filename, "w")
        try:
            ignore = None
            ignore_filename = "%s.ignore" % export_filename
            if os.path.exists(ignore_filename):
                _logger.info("Load ignore file %s" % ignore_filename)
                ignore = set()
                fileobj = misc.file_open(ignore_filename)
                reader = tools.TinyPoFile(fileobj)
                for row in reader:
                    if not row[4]:
                        ignore.add(row)

            _logger.info("Writing %s", export_filename)
            tools.trans_export(self.lang, [self.params.module], export_f, "po", self.cr, ignore=ignore)
        finally:
            export_f.close()


class Po_Import(Po_Export):
    """ Import *.po File """

    def __init__(self):
        super(Po_Import, self).__init__()
        self.parser.add_argument(
            "--overwrite", action="store_true", default=True, help="Override existing translations"
        )

    def run_config_env(self):
        # check module installed
        self.model_obj = self.pool["ir.module.module"]
        if not self.model_obj.search_id(self.cr, 1, [("state", "=", "installed"), ("name", "=", self.params.module)]):
            _logger.error("No module %s installed!" % self.params.module)
            return

        import_filename = os.path.join(self.langdir, self.langfile)
        if not os.path.exists(import_filename):
            _logger.error("File %s does not exist!" % import_filename)
            return

        # import
        context = {"overwrite": self.params.overwrite}
        if self.params.overwrite:
            _logger.info("Overwrite existing translations for %s/%s", self.params.module, self.lang)
        openerp.tools.trans_load(self.cr, import_filename, self.lang, module_name=self.params.module, context=context)


class Po_Cleanup(Po_Export):
    """ Import *.po File """

    def __init__(self):
        super(Po_Cleanup, self).__init__()

    def run_config_env(self):
        # check module installed
        self.model_obj = self.pool["ir.module.module"]
        if not self.model_obj.search_id(self.cr, 1, [("state", "=", "installed"), ("name", "=", self.params.module)]):
            _logger.error("No module %s installed!" % self.params.module)
            return

        import_filename = os.path.join(self.langdir, self.langfile)
        if not os.path.exists(import_filename):
            _logger.error("File %s does not exist!" % import_filename)
            return

        cr = self.cr
        with open(import_filename) as f:
            tf = TinyPoFile(f)
            for trans_type, name, res_id, source, trad, comments in tf:
                if not trad:
                    _logger.info("DELETE %s,%s" % (source, self.lang))

                    cr.execute(
                        """DELETE FROM ir_translation WHERE src=%s 
                              AND lang=%s 
                              AND module IS NULL 
                              AND type='code' 
                              AND value IS NOT NULL""",
                        (source, self.lang),
                    )

                    cr.execute(
                        """DELETE FROM ir_translation WHERE src=%s 
                              AND lang=%s 
                              AND module IS NULL 
                              AND value=%s""",
                        (source, self.lang, source),
                    )


class Test(ConfigCommand):
    """ Import *.po File """

    def __init__(self):
        super(Test, self).__init__()
        self.parser.add_argument(
            "--test-prefix",
            metavar="TEST_PREFIX",
            required=False,
            help="Specify the prefix of the method for filtering",
        )
        self.parser.add_argument("--test-case", metavar="TEST_CASE", required=False, help="Specify the test case")
        self.parser.add_argument(
            "--test-download",
            metavar="TEST_DOWNLOAD",
            required=False,
            help="Specify test download diretory (e.g. for reports)",
        )
        self.parser.add_argument("--test-log", metavar="TEST_LOG", required=False, help="Specify test log file")

    def run_config(self):
        dirServer = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../.."))
        dirWorkspace = os.path.abspath(os.path.join(dirServer, ".."))
        
        config["testing"] = True
        if self.params.test_download:
            config["test_download"] = self.params.test_download
        else:
            temp_dir = os.path.join(dirWorkspace,"temp")
            if not os.path.exists(temp_dir):
                _logger.info("Create temp directory %s" % temp_dir)
                os.mkdir(temp_dir)

            test_dir = os.path.join(temp_dir, "test")
            if not os.path.exists(test_dir):
                _logger.info("Create test directory %s" % test_dir)
                os.mkdir(test_dir)

            config["test_download"] = test_dir
        
        _logger.info("Test directory: %s" % config["test_download"])        
 
        # run with env
        self.setup_env()

    # for model in self.models.itervalues():
    def run_test(self, module_name, test_prefix=None, test_case=None):
        global current_test
        current_test = module_name

        def unwrap_suite(test):
            """
        Attempts to unpack testsuites (holding suites or cases) in order to
        generate a single stream of terminals (either test cases or customized
        test suites). These can then be checked for run/skip attributes
        individually.
    
        An alternative would be to use a variant of @unittest2.skipIf with a state
        flag of some sort e.g. @unittest2.skipIf(common.runstate != 'at_install'),
        but then things become weird with post_install as tests should *not* run
        by default there
        """
            if isinstance(test, unittest.TestCase):
                yield test
                return

            subtests = list(test)
            # custom test suite (no test cases)
            if not len(subtests):
                yield test
                return

            for item in itertools.chain.from_iterable(itertools.imap(unwrap_suite, subtests)):
                yield item

        def runnable(test):
            if not test_prefix or not isinstance(test, unittest.TestCase):
                if not test_case:
                    return True
                return type(test).__name__ == test_case
            return test._testMethodName.startswith(test_prefix)

        mods = get_test_modules(current_test)
        threading.currentThread().testing = True
        r = True
        for m in mods:
            tests = unwrap_suite(unittest2.TestLoader().loadTestsFromModule(m))
            suite = unittest2.TestSuite(itertools.ifilter(runnable, tests))

            if suite.countTestCases():
                t0 = time.time()
                t0_sql = openerp.sql_db.sql_counter
                _logger.info("%s running tests.", m.__name__)
                result = unittest2.TextTestRunner(verbosity=2, stream=TestStream(m.__name__)).run(suite)
                if time.time() - t0 > 5:
                    _logger.log(
                        25,
                        "%s tested in %.2fs, %s queries",
                        m.__name__,
                        time.time() - t0,
                        openerp.sql_db.sql_counter - t0_sql,
                    )
                if not result.wasSuccessful():
                    r = False
                    _logger.error(
                        "Module %s: %d failures, %d errors", module_name, len(result.failures), len(result.errors)
                    )

        current_test = None
        threading.currentThread().testing = False
        return r

    def run_config_env(self):
        if self.params.test_log:
            _logger.info("Log to file %s" % self.params.test_log)
            fh = logging.FileHandler(self.params.test_log)
            fh.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(message)s')
            fh.setFormatter(formatter)            
            logging.getLogger().addHandler(fh)

        test_prefix = self.params.test_prefix
        test_case = self.params.test_case
        cr = self.cr
        
        if self.params.module:
            modules = [m.strip() for m in self.params.module.split(",")]
        else:
            cr.execute("SELECT name from ir_module_module WHERE state = 'installed' ")
            modules = [name for (name,) in cr.fetchall()]

        if modules:
            for module_name in modules:
                ok = self.run_test(module_name, test_prefix, test_case)
                if not ok:
                    _logger.info("Failed!")
                    return False

            _logger.info("Finished!")
        else:
            _logger.warning("No Tests!")

        return True


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
        self.clean = True

    def fixable(self, msg):
        self.clean = False
        if self.params.fix:
            _logger.info("[FIX] %s" % msg)
        else:
            _logger.warning("[FIXABLE] %s" % msg)

    def notfixable(self, msg):
        self.clean = False
        _logger.warning("[MANUAL FIX] %s" % msg)

    def cleanup_translation(self):
        self.cr.execute(
            "SELECT id, lang, name, res_id, module FROM ir_translation WHERE type='model' ORDER BY lang, module, name, res_id, id"
        )
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
                    model_obj = self.pool.get(name)

                    # ignore uninstalled modules
                    if not model_obj or not model_obj._table:
                        continue

                    self.cr.execute("SELECT COUNT(id) FROM %s WHERE id=%s" % (model_obj._table, res_id))
                    if self.cr.fetchone()[0]:
                        ref_valid = True

                    refs[ref] = ref_valid

                # check if it is to delete
                if not ref_valid:
                    self.fixable("Translation object %s,%s no exist" % (name, res_id))
                    self.cr.execute("DELETE FROM ir_translation WHERE id=%s", (row[0],))

    def cleanup_double_translation(self):
        # check model translations
        self.cr.execute(
            "SELECT id, lang, name, res_id, module FROM ir_translation WHERE type='model' ORDER BY lang, module, name, res_id, id"
        )
        last_key = None
        first_id = None
        for row in self.cr.fetchall():
            key = row[1:]
            if last_key and cmp(key, last_key) == 0:
                self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                self.cr.execute("DELETE FROM ir_translation WHERE id=%s", (row[0],))
            else:
                first_id = row[0]
            last_key = key

        # check view translations
        self.cr.execute(
            "SELECT id, lang, name, src, module FROM ir_translation WHERE type='view' AND res_id=0 ORDER BY lang, module, name, src, id"
        )
        last_key = None
        first_id = None
        for row in self.cr.fetchall():
            key = row[1:]
            if last_key and cmp(key, last_key) == 0:
                self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                self.cr.execute("DELETE FROM ir_translation WHERE id=%s", (row[0],))
            else:
                first_id = row[0]
            last_key = key

        # show manual fixable
        self.cr.execute(
            "SELECT id, lang, name, res_id FROM ir_translation WHERE type='model' AND NOT name LIKE 'ir.model%' ORDER BY lang, name, res_id, id"
        )
        last_key = None
        first_id = None
        for row in self.cr.fetchall():
            key = row[1:]
            if last_key and cmp(key, last_key) == 0:
                if self.params.delete_lower and first_id < row[0]:
                    self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                    self.cr.execute("DELETE FROM ir_translation WHERE id=%s", (first_id,))
                    first_id = row[0]
                elif self.params.delete_higher and first_id > row[0]:
                    self.fixable("Double Translation %s for ID %s" % (repr(row), first_id))
                    self.cr.execute("DELETE FROM ir_translation WHERE id=%s", (first_id,))
                    first_id = row[0]
                else:
                    self.notfixable("Double Translation %s for ID %s" % (repr(row), first_id))
            else:
                first_id = row[0]
            last_key = key

    def delete_model(self, model):
        self.deleted_models[model.id] = model.model
        self.fixable("Delete model %s,%s" % (model.model, model.id))

        model_constraint_obj = self.pool["ir.model.constraint"]
        constraint_ids = model_constraint_obj.search(self.cr, SUPERUSER_ID, [("model", "=", model.id)])
        for constraint in model_constraint_obj.browse(self.cr, SUPERUSER_ID, constraint_ids):
            self.fixable("Delete model constraint %s,%s" % (constraint.name, constraint.id))
            constraint.unlink()

        model_access_obj = self.pool["ir.model.access"]
        access_ids = model_access_obj.search(self.cr, SUPERUSER_ID, [("model_id", "=", model.id)])
        for access in model_access_obj.browse(self.cr, SUPERUSER_ID, access_ids):
            self.fixable("Delete model access %s,%s" % (access.name, access.id))
            access.unlink()

        model_rel_obj = self.pool["ir.model.relation"]
        rel_ids = model_rel_obj.search(self.cr, SUPERUSER_ID, [("model", "=", model.id)])
        for rel in model_rel_obj.browse(self.cr, SUPERUSER_ID, rel_ids):
            self.fixable("Delete model relation %s,%s" % (rel.name, rel.id))
            rel.unlink()

        model_data_obj = self.pool["ir.model.data"]
        data_ids = model_data_obj.search(self.cr, SUPERUSER_ID, [("model", "=", model.model)])
        for data in model_data_obj.browse(self.cr, SUPERUSER_ID, data_ids):
            self.fixable("Delete model data %s,%s" % (data.name, data.id))
            data.unlink()

        model_field_obj = self.pool["ir.model.fields"]
        field_ids = model_field_obj.search(self.cr, SUPERUSER_ID, [("model_id", "=", model.id)])
        for field in model_field_obj.browse(self.cr, SUPERUSER_ID, field_ids):
            self.fixable("Delete model field %s,%s" % (field.name, field.id))
            self.cr.execute("DELETE FROM ir_model_fields WHERE id=%s", (field.id,))

        self.cr.execute(
            "SELECT id, name, type FROM ir_translation WHERE type IN ('model','field','view') AND name LIKE '%s%%'"
            % model.model
        )
        for oid, name, t in self.cr.fetchall():
            self.fixable("Delete model translation {id:%s|name:%s|type:%s}" % (oid, name, t))
            self.cr.execute("DELETE FROM ir_translation WHERE id=%s", (oid,))

        self.cr.execute("DELETE FROM ir_model WHERE id=%s", (model.id,))

    def delete_model_data(self, model_data):
        self.fixable(
            "Delete model_data %s,%s,%s,%s" % (model_data.name, model_data.id, model_data.model, model_data.res_id)
        )
        model_obj = self.pool.get(model_data.model)
        if model_obj and model_obj._name != "ir.model":
            self.fixable("Delete %s,%s" % (model_obj._name, model_data.res_id))
            model_obj.unlink(self.cr, SUPERUSER_ID, [model_data.res_id])
        model_data.unlink()

    def delete_module(self, module, full=False):
        self.deleted_modules[module.id] = module.name
        self.fixable("Delete module %s,%s" % (module.name, module.id))
        self.cr.execute("UPDATE ir_module_module SET state='uninstalled' WHERE id=%s", (module.id,))

        if full:
            model_data_obj = self.pool["ir.model.data"]
            model_data_ids = model_data_obj.search(self.cr, SUPERUSER_ID, [("module", "=", module.name)])

            for model_data in model_data_obj.browse(self.cr, SUPERUSER_ID, model_data_ids):
                self.delete_model_data(model_data)

        self.cr.execute(
            "DELETE FROM ir_module_module_dependency WHERE name=%s OR module_id=%s", (module.name, module.id)
        )
        self.cr.execute("DELETE FROM ir_module_module WHERE id=%s", (module.id,))
        self.cr.execute("DELETE FROM ir_model_data WHERE model='ir.module.module' AND res_id=%s", (module.id,))

    def cleanup_model_data(self):
        self.cr.execute(
            "SELECT d.id, d.model, d.res_id, d.name FROM ir_model_data d "
            " INNER JOIN ir_module_module m ON  m.name = d.module AND m.state='installed' "
            " WHERE d.res_id > 0 "
        )

        for oid, model, res_id, name in self.cr.fetchall():
            model_obj = self.pool[model]

            deletable = False
            if not model_obj:
                deletable = True
            else:
                self.cr.execute("SELECT id FROM %s WHERE id=%s" % (model_obj._table, res_id))
                if not self.cr.fetchall():
                    deletable = True

            if deletable:
                self.fixable("ir.model.data %s/%s (%s) not exist" % (model, res_id, name))
                self.cr.execute("DELETE FROM ir_model_data WHERE id=%s" % oid)

    def cleanup_modules(self):
        def getSet(value):
            if not value:
                return set()
            return set(re.split("[,|; ]+", value))

        mod_full_delete_set = getSet(self.params.full_delete_modules)
        mod_delete_set = getSet(self.params.delete_modules)

        module_obj = self.pool["ir.module.module"]
        module_ids = module_obj.search(self.cr, SUPERUSER_ID, [])
        for module in module_obj.browse(self.cr, SUPERUSER_ID, module_ids):
            info = openerp.modules.module.load_information_from_description_file(module.name)
            if not info:
                mod_full_delete = module.name in mod_full_delete_set
                mod_delete = module.name in mod_delete_set
                if mod_delete or mod_full_delete:
                    self.delete_module(module, mod_full_delete)
                else:
                    self.notfixable(
                        "Delete module %s dependencies and set uninstalled, but module is left in db" % module.name
                    )
                    self.cr.execute("UPDATE ir_module_module SET state='uninstalled' WHERE id=%s", (module.id,))
                    self.cr.execute(
                        "DELETE FROM ir_module_module_dependency WHERE name=%s OR module_id=%s",
                        (module.name, module.id),
                    )

        # check invalid module data
        self.cr.execute("SELECT id, res_id, name FROM ir_model_data WHERE model='ir.module.module' AND res_id > 0")
        for model_data_id, module_id, name in self.cr.fetchall():
            module_name = name[7:]
            self.cr.execute("SELECT id FROM ir_module_module WHERE id=%s", (module_id,))
            res = self.cr.fetchone()
            if not res:
                self.fixable("Module %s for module data %s not exist" % (module_name, model_data_id))
                self.cr.execute("DELETE FROM ir_model_data WHERE id=%s", (model_data_id,))

    def cleanup_models(self):
        model_obj = self.pool["ir.model"]
        model_ids = model_obj.search(self.cr, SUPERUSER_ID, [])
        for model in model_obj.browse(self.cr, 1, model_ids):
            if not self.pool.get(model.model):
                self.delete_model(model)

    def run_config(self):
        self.deleted_modules = {}
        self.deleted_models = {}

        # check full cleanup
        if self.params.full or self.params.only_models:

            # create registry
            self.pool = RegistryManager.get(self.params.database, update_module=True)
            self.cr = self.pool.cursor()
            # set auto commit to false
            self.cr.autocommit(False)

            try:

                # create environment
                with api.Environment.manage():
                    if self.params.only_models:
                        self.cleanup_models()
                    else:
                        self.cleanup_models()
                        self.cleanup_modules()
                        self.cleanup_model_data()
                        self.cleanup_translation()

                    if self.params.fix:
                        self.cr.commit()

            except Exception, e:
                _logger.error(e)
                return
            finally:
                self.cr.rollback()
                self.cr.close()

        if not self.params.only_models:
            # open database
            db = openerp.sql_db.db_connect(self.params.database)

            # basic cleanup's
            self.cr = db.cursor()
            self.cr.autocommit(False)
            try:
                self.cleanup_double_translation()
                if self.params.fix:
                    self.cr.commit()
            except Exception, e:
                _logger.error(e)
                return
            finally:
                self.cr.rollback()
                self.cr.close()
                self.cr = None

            if self.clean:
                _logger.info("Everything is CLEAN!")
            else:
                _logger.warning("Cleanup necessary")


class RemoveTestData(ConfigCommand):
    def delete_invoice(self):
        # reset move lines
        _logger.info("reset account_move_line to 'draft'")
        self.cr.execute("UPDATE account_move_line SET state='draft'")

        # reset moves
        _logger.info("reset account_move to 'draft'")
        self.cr.execute("UPDATE account_move SET state='draft'")

        # remove concilations
        reconcile_obj = self.env["account.move.reconcile"]
        self.cr.execute("SELECT id FROM account_move_reconcile")
        reconcile_ids = [r[0] for r in self.cr.fetchall()]
        for reconcile in reconcile_obj.browse(reconcile_ids):
            _logger.info("delete reconcile %s " % reconcile.name)
            reconcile.unlink()

        # unlink invoices
        self.cr.execute("SELECT id FROM account_invoice")
        invoice_ids = [r[0] for r in self.cr.fetchall()]
        invoice_obj = self.env["account.invoice"]
        for inv in invoice_obj.browse(invoice_ids):
            _logger.info("delete invoice %s " % inv.name)
            inv.internal_number = None
            inv.delete_workflow()
            inv.state = "draft"
            inv.unlink()

        # unlink moves
        self.cr.execute("SELECT id FROM account_move")
        move_ids = [r[0] for r in self.cr.fetchall()]
        move_obj = self.env["account.move"]
        for move in move_obj.browse(move_ids):
            _logger.info("delete move %s" % move.name)
            move.unlink()

        # unlink vouchers
        voucher_obj = self.env["account.voucher"]
        self.cr.execute("SELECT id FROM account_voucher")
        voucher_ids = [r[0] for r in self.cr.fetchall()]
        for voucher in voucher_obj.browse(voucher_ids):
            _logger.info("delete voucher %s " % voucher.id)
            voucher.cancel_voucher()
            voucher.unlink()

    def delete_procurement(self):
        proc_obj = self.env["procurement.order"]
        self.cr.execute("SELECT id FROM procurement_order")
        proc_ids = [r[0] for r in self.cr.fetchall()]
        for proc in proc_obj.browse(proc_ids):
            _logger.info("delete procurement %s " % proc.name)
            proc.state = "cancel"
            proc.unlink()

    def delete_stock(self):
        move_obj = self.env["stock.move"]

        _logger.info("reset stock move to 'draft'")
        self.cr.execute("UPDATE stock_move SET state='draft'")

        self.cr.execute("SELECT id FROM stock_move")
        move_ids = [r[0] for r in self.cr.fetchall()]
        for move in move_obj.browse(move_ids):
            _logger.info("delete stock move %s " % move.name)
            move.unlink()

        quant_obj = self.env["stock.quant"]
        self.cr.execute("SELECT id FROM stock_quant")
        quant_ids = [r[0] for r in self.cr.fetchall()]
        for quant in quant_obj.browse(quant_ids):
            _logger.info("delete quant %s " % quant.name)
            quant.unlink()

        pack_obj = self.env["stock.pack.operation"]
        self.cr.execute("SELECT id FROM stock_pack_operation")
        pack_ids = [r[0] for r in self.cr.fetchall()]
        for pack in pack_obj.browse(pack_ids):
            _logger.info("delete pack operation %s " % pack.id)
            pack.unlink()

        picking_obj = self.env["stock.picking"]
        self.cr.execute("SELECT id FROM stock_picking")
        picking_ids = [r[0] for r in self.cr.fetchall()]
        for picking in picking_obj.browse(picking_ids):
            _logger.info("delete picking %s" % picking.name)
            picking.action_cancel()
            picking.delete_workflow()
            picking.unlink()

    def delete_purchase(self):
        purchase_obj = self.env["purchase.order"]
        self.cr.execute("SELECT id FROM purchase_order")
        purchase_ids = [r[0] for r in self.cr.fetchall()]
        for purchase in purchase_obj.browse(purchase_ids):
            _logger.info("delete purchase %s " % purchase.name)
            purchase.delete_workflow()
            purchase.state = "cancel"
            purchase.unlink()

    def delete_hr(self):
        _logger.info("delete hr_attendance")
        self.cr.execute("DELETE FROM hr_attendance")

        timesheet_obj = self.env["hr_timesheet_sheet.sheet"]
        self.cr.execute("SELECT id FROM hr_timesheet_sheet_sheet")
        sheet_ids = [r[0] for r in self.cr.fetchall()]
        for sheet in timesheet_obj.browse(sheet_ids):
            _logger.info("delete sheet %s" % sheet.id)
            sheet.delete_workflow()
            sheet.state = "draft"
            sheet.unlink()

        expense_obj = self.env["hr.expense.expense"]
        self.cr.execute("SELECT id FROM hr_expense_expense")
        expense_ids = [r[0] for r in self.cr.fetchall()]
        for expense in expense_obj.browse(expense_ids):
            _logger.info("delete expense %s" % expense.name)
            expense.unlink()

    def delete_sale(self):
        # delete analytic lines
        self.cr.execute("DELETE FROM account_analytic_line")
        deleted_task = set()
        deleted_proj = set()

        # delete tasks
        def delete_task(tasks):
            for task in tasks:
                if not task.id in deleted_task:
                    deleted_task.add(task.id)
                    delete_task(task.child_ids)
                    _logger.info("delete task %s" % task.name)
                    task.unlink()

        # delete project
        def delete_project(proj):
            if not proj.id in deleted_proj:
                deleted_proj.add(proj.id)
                delete_task(project.tasks)
                _logger.info("delete project %s" % project.name)
                project.unlink()

        # delete task without project
        task_obj = self.env["project.task"]
        self.cr.execute("SELECT id FROM project_task WHERE project_id IS NULL")
        task_ids = [r[0] for r in self.cr.fetchall()]
        delete_task(task_obj.browse(task_ids))

        # delete project which are subproject form first default project
        project_obj = self.env["project.project"]
        projects = project_obj.search([("parent_id", "=", 1)])
        for project in projects:
            delete_project(project)

        # delete order and projects
        sale_obj = self.env["sale.order"]
        self.cr.execute("SELECT id FROM sale_order")
        order_ids = [r[0] for r in self.cr.fetchall()]
        for order in sale_obj.browse(order_ids):
            _logger.info("delete sale order %s" % order.name)
            order.action_cancel()
            project = order.order_project_id
            if project:
                delete_project(project)
            order.unlink()

    def run_config_env(self):
        self.delete_invoice()
        self.delete_procurement()
        self.delete_stock()
        self.delete_purchase()
        self.delete_sale()
        self.delete_hr()

    def run_config(self):
        self.setup_env()


class Console(ConfigCommand):
    def run_config_env(self):
        pass

    def run_config(self):
        pass

    def open(self):
        self.pool = RegistryManager.get(self.params.database)
        self.cr = self.pool.cursor()
        # api.Environment.manage()

    def close(self):
        pass


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

        lib_path = os.path.join(virtual_env, "lib", "python2.7")
        lib_path_openerp = os.path.join(lib_path, "openerp")
        lib_path_addons = os.path.join(lib_path_openerp, "addons")
        bin_path = os.path.join(virtual_env, "bin")

        # create directories
        for dir_path in (lib_path_openerp, lib_path_addons):
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
            dir_openerp = os.path.join(dirServer, "openerp")
            dir_openerp_addons = os.path.join(dir_openerp, "addons")
            old_addons = getAddonsSet()

            # setup odoo libs

            linkDirectoryEntries(dir_openerp, lib_path_openerp, ignore="addons")
            linkedBaseEntries = linkDirectoryEntries(dir_openerp_addons, lib_path_addons)

            # setup odoo bin

            odoo_bin = os.path.join(dirServer, "odoo-bin")
            linkFile(odoo_bin, os.path.join(bin_path, "odoo-bin"))

            # setup sitecustomize

            sitecustomize = os.path.join(dirServer, "sitecustomize.py")
            linkFile(sitecustomize, os.path.join(lib_path, "sitecustomize.py"))

            # setup additional libraries

            linkDirectoryEntries(
                dirServer, lib_path, names=["openerplib", "psycogreen", "pygal", "tinyrest", "aeroolib", "couchdb"]
            )

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
                                    curAddonPathMeta = os.path.join(curAddonPath, MANIFEST)
                                    if os.path.exists(curAddonPathMeta):
                                        addonMeta = None
                                        with open(curAddonPathMeta) as metaFp:
                                            addonMeta = eval(metaFp.read())

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

        args, unknown = parser.parse_known_args(args=cmdargs)

        dir_server = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../.."))
        dir_workspace = os.path.abspath(os.path.join(dir_server, ".."))

        if args.path:
            dir_workspace = os.path.abspath(os.path.expanduser(os.path.expandvars(args.path)))

        # get addons paths
        if "--addons-path" not in cmdargs:
            addon_pattern = [dir_workspace + "/addons*"]
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
                except DatabaseExists, e:
                    pass
                except Exception, e:
                    die("Could not create database `%s`. (%s)" % (args.db_name, e))

            if "--db-filter" not in cmdargs:
                cmdargs.append("--db-filter=^%s$" % args.db_name)

        # Remove --path /-p options from the command arguments
        def to_remove(i, l):
            return l[i] == "-p" or l[i].startswith("--path") or (i > 0 and l[i - 1] in ["-p", "--path"])

        cmdargs = [v for i, v in enumerate(cmdargs) if not to_remove(i, cmdargs)]

        main(cmdargs)


def die(message, code=1):
    print >>sys.stderr, message
    sys.exit(code)
