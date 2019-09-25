# -*- coding: utf-8 -*-
"""
Created: 2018
Last Updated: 2018-09-07
ArcGIS version: ArcGIS Pro
Python version: Python 3.5.3
Author: David Bucklin

Collection of helper functions used by
functions in this repository.
"""

import arcpy
arcpy.CheckOutExtension("Spatial")
from arcpy.sa import *
import os
import time
import re

def unique_values(table, field):
   ''' Gets list of unique values in a field.
   Thanks, ArcPy Cafe! https://arcpy.wordpress.com/2012/02/01/create-a-list-of-unique-field-values/'''
   with arcpy.da.SearchCursor(table, [field]) as cursor:
      return sorted({row[0] for row in cursor})

def make_gdb(path):
   ''' Creates a geodatabase if it doesn't exist'''
   path = path.replace("\\", "/")
   if '.gdb' not in path:
      print("Bad geodatabase path name.")
      return False
   folder = path[0:path.rindex("/")]
   name = path[(path.rindex("/")+1):len(path)]
   if not os.path.exists(path):
      try:
         arcpy.CreateFileGDB_management(folder, name)
      except:
         return False
      else:
         print("Geodatabase '" + path + "' created.")
         return True
   else:
      return True

def make_gdb_name(string):
   '''Makes strings GDB-compliant'''
   nm = re.sub('[^A-Za-z0-9]+', '_', string)
   return nm

def garbagePickup(trashList):
   '''Deletes Arc files in list, with error handling. Argument must be a list.'''
   for t in trashList:
      try:
         arcpy.Delete_management(t)
      except:
         pass
   return

def valToScoreNeg(inRast, minimum, maximum):
   '''Given an input value raster, applies a negative function so that the model score at or below the minimum value is 100,
   with scores decreasing to 0 at the maximum value and beyond.'''
   rast = Con(inRast <= minimum, 100, Con(inRast > maximum, 0, 100 * (maximum - inRast) / (maximum - minimum)))
   return rast