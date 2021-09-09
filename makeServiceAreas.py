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
      4. 'round'. This is like None, but will round to the nearest integer.
"""

import sys
import arcpy
import os
import time
import re
import numpy as np
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


def roundRast(inRast, outRast, digits=0, reset_to=None):
   # Round or reset to a constant value, any non-NoData value in a raster.
   
   ras = arcpy.sa.Raster(inRast)
   ll = arcpy.Point(ras.extent.XMin, ras.extent.YMin)
   cs = ras.meanCellWidth

   r = arcpy.RasterToNumPyArray(ras, nodata_to_value=-999)
   if reset_to:
      r1 = np.where(r != -999, reset_to, -999)
   else:
      if digits == 0:
         r1 = r.round(digits).astype(int)
      else:
         r1 = r.round(digits)
   r2 = arcpy.NumPyArrayToRaster(r1, ll, cs, value_to_nodata=-999)
   r2.save(outRast)

   return outRast


def makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld=None, maxCost=None,
                     attFld=None, featNm='accFeat_orig'):

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

   print('Checking if project geodatabase exists...')
   make_gdb(outGDB)
   # Create a processing-only GDB, which is only for in-function usage. Helps avoid issues with locks and corrupted GDBs.
   print('Making a processing geodatabase...')
   pgdb = outGDB.replace('.gdb', '') + '_proc.gdb'
   arcpy.Delete_management(pgdb)
   make_gdb(pgdb)
   arcpy.env.workspace = outGDB
   arcpy.SetLogHistory(False)

   # copy access features to gdb
   if not arcpy.Exists(featNm):
      arcpy.CopyFeatures_management(accFeat, featNm)
   if not grpFld:
      # add a field to assign all rows to one group.
      grpFld = 'serviceArea_group'
      arcpy.CalculateField_management(featNm, grpFld, "1", field_type="SHORT")
   grps = unique_values(featNm, grpFld)

   # assign max costs
   if maxCost:
      if isinstance(maxCost, str):
         arcpy.CalculateField_management(featNm, 'minutes_SA', '!' + maxCost + '!', field_type="FLOAT")
      else:
         arcpy.CalculateField_management(featNm, 'minutes_SA', maxCost, 'PYTHON', field_type="FLOAT")
      # dictionary: grps: minutes
      grp_min = {a[0]: a[1] for a in arcpy.da.SearchCursor(featNm, [grpFld, 'minutes_SA'])}

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

      arcpy.Select_analysis(featNm, cdpts, grpFld + " = " + str(i_q))
      print('Number of access features: ' + arcpy.GetCount_management(cdpts)[0])

      # get service area in minutes
      if maxCost is not None:
         grpMaxCost = grp_min[i]
         # Make buffer to set a smaller extent, to reduce processing time.
         buffd = str(int(grpMaxCost * 1500)) + ' METERS'  # buffer set to hypothetical travel in a STRAIGHT LINE at ~56 mph. 
         print('Cost in minutes: ' + str(grpMaxCost))
         arcpy.Buffer_analysis(cdpts, "buffext", buffd)
         arcpy.env.extent = "buffext"
      else:
         grpMaxCost = None

      # local CD
      cd1 = arcpy.sa.CostDistance(cdpts, costRastLoc, grpMaxCost)
      nm = pgdb + os.sep + "cd" + str(c)
      cd1.save(nm)
      cds = [nm]

      # values to ramps
      rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd1, pgdb + os.sep + "rp1", "NONE", "VALUE_ONLY")
      rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")

      if int(arcpy.GetCount_management(rp1s)[0]) == 0:
         # No ramps reached: just output local roads only service area
         if attFld is not None:
            if isinstance(attFld, str):
               if attFld == 'round':
                  roundRast(pgdb + os.sep + "cd1", rastout, digits=0)
               else:
                  areaval = round(unique_values(cdpts, attFld)[0], 3)
                  roundRast(pgdb + os.sep + "cd1", rastout, reset_to=areaval)
            elif isinstance(attFld, int):
               roundRast(pgdb + os.sep + "cd1", rastout, reset_to=attFld)
         else:
            cd1.save(rastout)
      else:
         # Some ramps reached: Run highways/local loop until there is no improvement in travel time.
         notin = [1]
         while len(notin) != 0:
            print('Limited-access cost distance run # ' + str(int((c+1)/2)) + '...')
            arcpy.CopyFeatures_management(rp1s, pgdb + os.sep + "rp1s")

            # highway CD
            cd2 = arcpy.sa.CostDistance(pgdb + os.sep + "rp1s", costRastHwy, grpMaxCost, source_start_cost="RASTERVALU")
            c += 1
            nm = pgdb + os.sep + "cd" + str(c)
            cd2.save(nm)
            cds = cds + [nm]

            rp2 = arcpy.sa.ExtractValuesToPoints(rampPts, cd2, pgdb + os.sep + "rp2", "NONE", "VALUE_ONLY")
            # change name to avoid confusion with local ramp points
            arcpy.AlterField_management(rp2, "RASTERVALU", "costLAH", clear_field_alias=True)
            rp2s = arcpy.MakeFeatureLayer_management(rp2, where_clause="costLAH IS NOT NULL")

            # Check for new ramps or ramps reached at least one minute faster after latest run (LAH)
            notin = []
            lahr = {a[0]: a[1] for a in arcpy.da.SearchCursor(rp2s, [rampPtsID, 'costLAH'])}
            locr = {a[0]: a[1] for a in arcpy.da.SearchCursor(pgdb + os.sep + 'rp1s', [rampPtsID, 'RASTERVALU'])}
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
            arcpy.CopyFeatures_management(rp2s, pgdb + os.sep + "rp2s")
            cd3 = arcpy.sa.CostDistance(pgdb + os.sep + "rp2s", costRastLoc, grpMaxCost, source_start_cost="costLAH")
            c += 1
            nm = pgdb + os.sep + "cd" + str(c)
            cd3.save(nm)
            cds = cds + [nm]

            rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd3, pgdb + os.sep + "rp1", "NONE", "VALUE_ONLY")
            rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")

            # Check for new ramps or ramps reached at least one minute faster after latest run (Local)
            # Similar to process earlier, but with names reversed
            notin = []
            locr = {a[0]: a[1] for a in arcpy.da.SearchCursor(rp1s, [rampPtsID, 'RASTERVALU'])}
            lahr = {a[0]: a[1] for a in arcpy.da.SearchCursor(pgdb + os.sep + 'rp2s', [rampPtsID, 'costLAH'])}
            for a in locr:
               if a not in lahr:
                  notin.append(a)
               else:
                  if locr[a] - lahr[a] < -1:
                     notin.append(a)
            # end while loop

         if attFld is not None:
            if isinstance(attFld, str):
               if attFld == 'round':
                  roundRast(arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA"), rastout, digits=0)
               else:
                  # cell statistics
                  areaval = round(unique_values(cdpts, attFld)[0], 3)
                  roundRast(arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA"), rastout, reset_to=areaval)
            elif isinstance(attFld, int):
               roundRast(arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA"), rastout, reset_to=attFld)
         else:
            arcpy.sa.CellStatistics(cds, "MINIMUM", "DATA").save(rastout)

      print("Done with group: " + str(i))
      t1 = time.time()
      print('That took ' + str(int(t1 - t0)) + ' seconds.')

   # delete processing geodatabase
   arcpy.Delete_management(pgdb)
   # reset extent
   arcpy.env.extent = costRastLoc

   return rastout


def main():
   # General usage

   # Environment settings
   arcpy.env.parallelProcessingFactor = "100%"  # Adjust to some percent (e.g. 100%) for large extent analyses (e.g. for maxCost = None)
   arcpy.env.mask = r'L:\David\projects\RCL_processing\RCL_processing.gdb\VA_Buff50mi_wgs84'
   arcpy.env.overwriteOutput = True

   # Cost surface variables
   costRastLoc = r'E:\RCL_cost_surfaces\Tiger_2019\cost_surfaces.gdb\costSurf_no_lah'
   costRastHwy = r'E:\RCL_cost_surfaces\Tiger_2019\cost_surfaces.gdb\costSurf_only_lah'
   rampPts = r'E:\RCL_cost_surfaces\Tiger_2019\cost_surfaces.gdb\rmpt_final'
   rampPtsID = 'UniqueID'  # unique ramp segment ID attribute field, since some ramps have multiple points

   # Facilities features and settings
   accFeat = r'accessFeatures'
   outGDB = r'serviceAreas.gdb'
   # Attributes
   grpFld = 'facil_code'
   maxCost = 30
   attFld = None
   makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld, maxCost, attFld)


if __name__ == '__main__':
   main()
