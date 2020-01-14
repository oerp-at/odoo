#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, stat
import fnmatch
import logging as log
import glob

import shutil

ADDON_META = "__manifest__.py"
ADDON_API = 13

VERSION = '%s.0' % ADDON_API
SERVER_CONF = "server.conf"
DIR_DIST_ADDONS = os.path.expanduser('~/.local/share/Odoo/addons/%s' % VERSION)
DIR_SERVER = os.path.abspath(os.path.dirname(os.path.realpath( __file__ )))
DIR_WORKSPACE = os.path.abspath(os.path.join(DIR_SERVER,".."))

ADDONS_IGNORED = []
ADDONS_INCLUDED = {
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
      os.path.join(DIR_WORKSPACE,"addons*")
    ]

    addons = []
    for curPattern in addonPattern:
        curDir = os.path.dirname(curPattern)
        for curAddonPackageDir in glob.glob(curPattern):
            curPackagePath = os.path.join(curDir,curAddonPackageDir)
            if os.path.isdir(curPackagePath):
                for curAddon in listDir(curPackagePath):
                    enabledPath = os.path.join(DIR_DIST_ADDONS,curAddon)
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
    dirWorkspace = DIR_WORKSPACE
    dirEnabledAddons = DIR_DIST_ADDONS
        
    #create path if not exists
    if not os.path.exists(dirEnabledAddons):
        log.info("Create directory %s" % dirEnabledAddons)
        os.makedirs(dirEnabledAddons)
        
    os.chmod(dirEnabledAddons, 0o710)

    filesToCopy = [
    ]

    addonPattern = [
      dirWorkspace+"/addons*"
    ]

    ignoreAddons = ADDONS_IGNORED
    includeAddons = ADDONS_INCLUDED
    merged = []
    updateFailed = []
    
    if not onlyLinks:
      log.info("Cleanup all *.pyc Files")
      cleanupPython(dirWorkspace)

    if not os.path.exists(dirEnabledAddons):
        log.info("Create directory %s" % str(dirEnabledAddons))
        os.makedirs(dirEnabledAddons)

    t_removedLinks = 0
    t_addedLinks = 0

    dir_processed = set()
    dir_removed = set()
    dir_added = set()

    log.info("Delete current Symbolic links and distributed files " + str(dirEnabledAddons) + " ...")
    for curLink in glob.glob(dirEnabledAddons+'/*'):
        curLinkPath = os.path.join(dirEnabledAddons,curLink)
        #log.info("Found Link " + str(curLinkPath))
        is_link = os.path.islink(curLinkPath)
        if is_link: #or os.path.isfile(curLinkPath):
            #log.info("Remove link " + str(curLinkPath))
            os.remove(curLinkPath)
            if is_link:
                t_removedLinks+=1
                dir_removed.add(os.path.basename(curLink))

    log.info("Distribute Files " + str(dirEnabledAddons) + " ...")
    for fileToCopy in filesToCopy:
        fileToCopyPath = os.path.join(dirServer,fileToCopy)
        if os.path.exists(fileToCopyPath):
            fileDestPath = os.path.join(dirEnabledAddons,os.path.basename(fileToCopyPath))
            log.info("Copy File %s to %s " % (fileToCopyPath,fileDestPath))
            shutil.copyfile(fileToCopyPath, fileDestPath)

    #link per addons basis
    for curPattern in addonPattern:
        for curAddonPackageDir in glob.glob(curPattern):
            packageName = os.path.basename(curAddonPackageDir)
            if not curAddonPackageDir in dir_processed:
                dir_processed.add(curAddonPackageDir)
                log.info("Process: " + curAddonPackageDir)
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
                                        t_addedLinks += 1
                                        dir_added.add(curAddon)

            else:
                #log.info("processed twice: " + curAddonPackageDir)
                pass

    for cur_dir in dir_removed:
        if not cur_dir in dir_added:
            log.info("Removed Addin: " + cur_dir)

    for cur_dir in dir_added:
        if not cur_dir in dir_removed:
            log.info("Addin Added: " + cur_dir)

    if merged:
        log.info("\n\nMerged:\n * %s\n" % ("\n * ".join(merged),))

    if updateFailed:
        log.error("\n\nUnable to update:\n * %s\n" % ("\n * ".join(updateFailed),))

    log.info("Removed links: " + str(t_removedLinks))
    log.info("Added links: "  + str(t_addedLinks))
    log.info("Finished!")
    

if __name__ == "__main__":
    log.basicConfig(level=log.INFO,
             format='%(asctime)s %(levelname)s %(message)s')
    
   
    setup()      