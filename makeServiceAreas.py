# -*- coding: utf-8 -*-
"""
makeServiceAreas

Created: 2018-07
Last Updated: 2020-10-23
ArcGIS version: ArcGIS Pro 2.6
Python version: Python 3.6.6
Author: David Bucklin

Raster-based approach for building service areas, using a two raster cost-surface (local roads and limited access
highways) and connection points approach. Cost distance is run iteratively on (1) local and (2) limited access
roads until the maximum cost is reached, with (rampPts) defining where local roads and limited access roads
are connected.

Argument definitions:
outGDB: Name of output geodatabase, which is created during the process.
accFeat: Access features to run cost distance process on
costRastLoc: A cost surface for all local (non-limited access) roads
costRastHwy: A cost surface for all limited access roads
rampPts: A point feature class defining connection points between
   local and limited access roads.
rampPtsID: a unique id corresponding to a given ramp/connection
grpFld: The grouping attribute field name for accFeat, where one cost distance is
   run for each group. Default (None) is to treat all features as one group.
maxCost: the maximum cost distance allowed. Can be:
      1. a string indicating the column in 'accFeat' which contains the numeric values to use as maximum costs.
      2. a numeric value indicating the maximum cost to apply to all service areas
      3. None (empty). No maximum distance. Note that this computes cost distance for the full extent of costRastLoc
attFld: Optional. A score value to apply to the service area raster. Can be:
      1. a string indicating the column in 'accFeat' which contains the numeric value to apply.
      2. an integer value, applied as a constant to the service area raster.
      3. None (empty). The original cost distance raster is returned (value = the cost distance).
"""

import sys
import arcpy
import os
import time
import re
arcpy.CheckOutExtension("Spatial")


def unique_values(table, field):
   """ Gets list of unique values in a field.
   Thanks, ArcPy Cafe! https://arcpy.wordpress.com/2012/02/01/create-a-list-of-unique-field-values/"""
   with arcpy.da.SearchCursor(table, [field]) as cursor:
      return sorted({row[0] for row in cursor})


def make_gdb(path):
   """ Creates a geodatabase if it doesn't exist"""
   path = path.replace("\\", "/")
   if '.gdb' not in path:
      print("Bad geodatabase path name.")
      return False
   folder = path[0:path.rindex("/")]
   name = path[(path.rindex("/") + 1):len(path)]
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
   """Makes strings GDB-compliant"""
   nm = re.sub('[^A-Za-z0-9]+', '_', string)
   return nm


def garbagePickup(trashList):
   """Deletes Arc files in list, with error handling. Argument must be a list."""
   for t in trashList:
      try:
         arcpy.Delete_management(t)
      except:
         pass
   return


def makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld=None, maxCost=None, attFld=None):

   # Checks on attFld
   if attFld:
      if not maxCost:
         print('Must specify a `maxCost` value if using `attFld`, exiting...')
         return
      if isinstance(attFld, str) and not [attFld in [a.name for a in arcpy.ListFields(accFeat)]]:
         print('Field ' + attFld + ' not found in access features, exiting...')
         return

   arcpy.env.snapRaster = costRastLoc
   arcpy.env.cellSize = costRastLoc
   arcpy.env.extent = costRastLoc
   arcpy.env.outputCoordinateSystem = costRastLoc

   make_gdb(outGDB)
   arcpy.env.workspace = outGDB
   arcpy.SetLogHistory(False)

   # copy access points to gdb
   accFeat = arcpy.CopyFeatures_management(accFeat, 'accFeat_orig')
   if not grpFld:
      # add a field to assign all rows to one group.
      grpFld = 'serviceArea_group'
      arcpy.CalculateField_management(accFeat, grpFld, "1", field_type="SHORT")
   grps = unique_values(accFeat, grpFld)

   # assign max costs
   if maxCost:
      if isinstance(maxCost, str):
         arcpy.CalculateField_management(accFeat, 'minutes_SA', '!' + maxCost + '!', field_type="FLOAT")
      else:
         arcpy.CalculateField_management(accFeat, 'minutes_SA', maxCost, 'PYTHON', field_type="FLOAT")
      # dictionary: grps: minutes
      grp_min = {a[0]: a[1] for a in arcpy.da.SearchCursor(accFeat, [grpFld, 'minutes_SA'])}

   for i in grps:
      n = grps.index(i) + 1
      if isinstance(i, str):
         rastout = "grp_" + i + "_servArea"
         cdpts = "grp_" + i + "_inputFeat"
         i_q = "'" + i + "'"
      else:
         rastout = "grp_" + str(int(i)) + "_servArea"
         cdpts = "grp_" + str(int(i)) + "_inputFeat"
         i_q = i
      if arcpy.Exists(rastout):
         # skip already existing
         continue

      print("working on group " + str(i) + " (" + str(n) + " of " + str(len(grps)) + ")...")
      arcpy.env.extent = costRastLoc  # reset extent prior to every run
      t0 = time.time()
      c = 1  # counter

      arcpy.Select_analysis(accFeat, cdpts, grpFld + " = " + str(i_q))
      print('Number of access pts: ' + arcpy.GetCount_management(cdpts)[0])

      # get service area in minutes
      if maxCost is not None:
         grpMaxCost = grp_min[i]
         # Make buffer to set a smaller extent, to reduce processing time.
         buffd = str(int(grpMaxCost * 1609)) + ' METERS'  # buffer set to straightline distance at ~60 mph (1 mile per minute)
         print('Cost in minutes: ' + str(grpMaxCost))
         arcpy.Buffer_analysis(cdpts, "buffext", buffd)
         arcpy.env.extent = "buffext"
      else:
         grpMaxCost = None

      # local CD
      cd1 = arcpy.sa.CostDistance(cdpts, costRastLoc, grpMaxCost)
      nm = "cd" + str(c)
      cd1.save(nm)
      cds = [nm]

      # values to ramps
      rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd1, "rp1", "NONE", "VALUE_ONLY")
      rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")

      if int(arcpy.GetCount_management(rp1s)[0]) == 0:
         # No ramps reached: just output local roads only service area
         if attFld is not None:
            if isinstance(attFld, str):
               areaval = unique_values(cdpts, attFld)[0]
               area = arcpy.sa.Con("cd1", areaval, "", "Value <= " + str(grpMaxCost))
               area.save(rastout)
            elif isinstance(attFld, int):
               area = arcpy.sa.Con("cd1", attFld, "", "Value <= " + str(grpMaxCost))
               area.save(rastout)
         else:
            cd1.save(rastout)
      else:
         # Some ramps reached: Run highways/local loop until there is no improvement in travel time.
         notin = [1]
         while len(notin) != 0:
            print('Limited-access cost distance run # ' + str(int((c+1)/2)) + '...')
            arcpy.CopyFeatures_management(rp1s, "rp1s")

            # highway CD
            cd2 = arcpy.sa.CostDistance("rp1s", costRastHwy, grpMaxCost, source_start_cost="RASTERVALU")
            c += 1
            nm = "cd" + str(c)
            cd2.save(nm)
            cds = cds + [nm]

            rp2 = arcpy.sa.ExtractValuesToPoints(rampPts, cd2, "rp2", "NONE", "VALUE_ONLY")
            # change name to avoid confusion with local ramp points
            arcpy.AlterField_management(rp2, "RASTERVALU", "costLAH", clear_field_alias=True)
            rp2s = arcpy.MakeFeatureLayer_management(rp2, where_clause="costLAH IS NOT NULL")

            # Check for new ramps or ramps reached at least one minute faster after latest run (LAH)
            notin = []
            lahr = {a[0]: a[1] for a in arcpy.da.SearchCursor(rp2s, [rampPtsID, 'costLAH'])}
            locr = {a[0]: a[1] for a in arcpy.da.SearchCursor('rp1s', [rampPtsID, 'RASTERVALU'])}
            for a in lahr:
               if a not in locr:
                  notin.append(a)
               else:
                  if lahr[a] - locr[a] < -1:
                     notin.append(a)
            if len(notin) == 0:
               print('No new ramps reached after LAH, moving on...')
               break

            # back to local
            arcpy.CopyFeatures_management(rp2s, "rp2s")
            cd3 = arcpy.sa.CostDistance("rp2s", costRastLoc, grpMaxCost, source_start_cost="costLAH")
            c += 1
            nm = "cd" + str(c)
            cd3.save(nm)
            cds = cds + [nm]

            rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd3, "rp1", "NONE", "VALUE_ONLY")
            rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")

            # Check for new ramps or ramps reached at least one minute faster after latest run (Local)
            # Similar to process earlier, but with names reversed
            notin = []
            locr = {a[0]: a[1] for a in arcpy.da.SearchCursor(rp1s, [rampPtsID, 'RASTERVALU'])}
            lahr = {a[0]: a[1] for a in arcpy.da.SearchCursor('rp2s', [rampPtsID, 'costLAH'])}
            for a in locr:
               if a not in lahr:
                  notin.append(a)
               else:
                  if locr[a] - lahr[a] < -1:
                     notin.append(a)
            # end while loop

         if attFld is not None:
            if isinstance(attFld, str):
               # cell statistics
               areaval = round(unique_values(cdpts, attFld)[0], 3)
               area = arcpy.sa.Con(arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA"), areaval, "", "Value <= " + str(grpMaxCost))
               area.save(rastout)
            elif isinstance(attFld, int):
               area = arcpy.sa.Con(arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA"), attFld, "", "Value <= " + str(grpMaxCost))
               area.save(rastout)
         else:
            arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA").save(rastout)

      print("Done with group: " + str(i))
      t1 = time.time()
      print('That took ' + str(int(t1 - t0)) + ' seconds.')

      # garbage pickup every 10 runs, last run
      if n == round(n, -1) or n == len(grps):
         print("Deleting files...")
         r = arcpy.ListRasters("cd*")
         fc = arcpy.ListFeatureClasses("rp*")
         fc.append("buffext")
         garbagePickup(r)
         garbagePickup(fc)

   # reset extent
   arcpy.env.extent = costRastLoc

   arcpy.BuildPyramids_management(rastout)
   return rastout


# General usage

# # Environment settings
# arcpy.env.parallelProcessingFactor = "100%"  # Adjust to some percent (e.g. 100%) for large extent analyses (e.g. maxCost = None)
# arcpy.env.mask = r'L:\David\projects\RCL_processing\RCL_processing.gdb\VA_Buff50mi_wgs84'
# arcpy.env.overwriteOutput = True
#
# # Cost surface variables
# costRastLoc = r'E:\RCL_cost_surfaces\Tiger_2019\cost_surfaces.gdb\costSurf_no_lah'
# costRastHwy = r'E:\RCL_cost_surfaces\Tiger_2019\cost_surfaces.gdb\costSurf_only_lah'
# rampPts = r'E:\RCL_cost_surfaces\Tiger_2019\cost_surfaces.gdb\rmpt_final'
# rampPtsID = 'UniqueID'  # unique ramp segment ID attribute field, since some ramps have multiple points
#
# # Facilities features and settings
# accFeat = r'accessFeatures'
# outGDB = r'serviceAreas.gdb'
# # Attributes
# grpFld = 'facil_code'
# maxCost = 30
# attFld = None
# makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld, maxCost, attFld)