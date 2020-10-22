# -*- coding: utf-8 -*-
"""
makeServiceAreas

Created: 2018-07
Last Updated: 2020-10-22
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
   run for each group
maxCost: the maximum cost distance allowed. Can be:
      1. a string indicating the column in 'accFeat' which contains the numeric values to use as maximum costs.
      2. a numeric value indicating the maximum cost to apply to all service areas
      3. None (empty). No maximum distance. Note that this computes cost distance for the full extent of costRastLoc
attFld: Optional. A score value to apply to the service area raster. Can be:
      1. a string indicating the column in 'accFeat' which contains the numeric value to apply.
      2. an integer value, applied as a constant to the service area raster.
      3. None (empty). The original cost distance raster is returned (value = the cost distance).
"""

from Helper import *


def makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld, maxCost=None, attFld=None):

   if attFld and not maxCost:
      print('Must specify a `maxCost` value if using `attFld`, exiting...')
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
   grps = unique_values(accFeat, grpFld)

   # assign max costs
   if maxCost:
      if isinstance(maxCost, str):
         arcpy.CalculateField_management(accFeat, 'minutes_SA', '!' + maxCost + '!', field_type="FLOAT")
      else:
         arcpy.CalculateField_management(accFeat, 'minutes_SA', maxCost, 'PYTHON', field_type="FLOAT")

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
      arcpy.env.extent = costRastLoc  # so points don't get excluded due to previous extent setting
      t0 = time.time()
      c = 1  # counter

      arcpy.Select_analysis(accFeat, cdpts, grpFld + " = " + str(i_q))
      print('Number of access pts: ' + arcpy.GetCount_management(cdpts)[0])

      # get service area in minutes
      if maxCost is not None:
         grpMaxCost = round(unique_values(cdpts, 'minutes_SA')[0], 1)
         buffd = str(int(grpMaxCost * 1750)) + ' METERS'  # buffer set to straightline distance at ~65 mph
         print('Cost in minutes: ' + str(grpMaxCost))
         arcpy.Buffer_analysis(cdpts, "buffpts", buffd)
         arcpy.env.extent = "buffpts"
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
            rp2s = arcpy.MakeFeatureLayer_management(rp2, where_clause="RASTERVALU IS NOT NULL")
            arcpy.CalculateField_management(rp2s, "costLAH", "!RASTERVALU!", field_type="DOUBLE")
            arcpy.CopyFeatures_management(rp2s, "rp2s")

            # back to local
            cd3 = arcpy.sa.CostDistance("rp2s", costRastLoc, grpMaxCost, source_start_cost="RASTERVALU")

            # write raster
            c += 1
            nm = "cd" + str(c)
            cd3.save(nm)
            cds = cds + [nm]

            rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd3, "rp1", "NONE", "VALUE_ONLY")
            rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")
            arcpy.CalculateField_management(rp1s, "costLoc", "!RASTERVALU!", field_type="DOUBLE")

            # join two ramp datasets together
            arcpy.JoinField_management(rp1s, rampPtsID, "rp2s", rampPtsID, ["costLAH"])

            # calculate time difference
            arcpy.CalculateField_management(rp1s, "diff", '!costLoc! - !costLAH!', 'PYTHON', field_type="DOUBLE")
            # if this value is < 0, then this resulted in a faster route to that point. Should be kept for next run.
            sc1s = arcpy.da.SearchCursor(rp1s, [rampPtsID, "diff"])

            # Add new ramps (None) or ramps with at least 1 minute faster route after local run.
            # This will lead to another LAH/local loop, starting from rp1s.
            # DO NOT use notin to select points, though.
            notin = []
            for r in sc1s:
               if r[1] is None or r[1] < -1:
                  notin.append(r[0])

         if attFld is not None:
            if isinstance(attFld, str):
               # cell statistics
               areaval = unique_values(cdpts, attFld)[0]
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
      if n == round(n, -1) or n == str(len(grps)):
         print("Deleting files...")
         r = arcpy.ListRasters("cd*")
         fc = arcpy.ListFeatureClasses("rp*")
         fc.append("buffpts")
         garbagePickup(r)
         garbagePickup(fc)

   # reset extent
   arcpy.env.extent = costRastLoc

   return

## end


def main():

   # Environment settings
   arcpy.env.parallelProcessingFactor = "100%"  # Adjust to some percent (e.g. 100%) for large extent analyses (e.g. maxCost = None)
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
