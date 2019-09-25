# -*- coding: utf-8 -*-
"""
makeServiceAreas

Created: 2018-07
Last Updated: 2019-09-25
ArcGIS version: ArcGIS Pro
Python version: Python 3.6.6
Author: David Bucklin

Raster-based approach for building service areas,
using a two raster cost-surface +
connection points approach.

Local roads (non-limited access highways)
and limited access highways. Cost distance
is run iteratively on (1) local and (2) limited access
roads until the maximum cost is reached, with connection
points (rampPts) defining where local roads and limited
access roads meet.

Argument definitions:
outGDB: Name of output geodatabase, which is created during the process.
   Note that once 500 groups are processed, a new geodatabase with the
   same name plus a sequential number is created.
accFeat: Access features to run cost distance process on
costRastLoc: A cost surface for all local (non-limited access) roads
costRastHwy: A cost surface for all limited access roads
rampPts: A point feature class defining connection points between
   local and limited access roads.
rampPtsID: a unique id corresponding to a given ramp/connection
maxCost: the maximum cost distance allowed
grpFld: The grouping attribute field name for accFeat, where one cost distance is
   run for each group
attFld: Optional. A score value to apply to the service area raster.
   'attFld' can be:
      1. a string indicating the column in 'accFeat' which contains the numeric value to apply.
         This option is customized for VA Recreation Model to calculate variable service area times (see codeblock).
      2. an integer value, applied as a constant to the service area raster.
      3. None (empty). The original cost distance raster is returned (value = the cost distance).

FIXME: Major slowdown in ArcGIS Pro with Cost Distance, after upgrading to v.2.3 (from 2.0).
 This function works well with 2.0, but has become unusable in newer versions.
 Not yet resolved, but issue submitted and under investigation by Esri.
 https://community.esri.com/thread/235940-noticing-a-major-slowdown-in-cost-distance-function-following-version-change
 Note the 'parallelProcessingFactor' environment settings could have some effect on this.
"""

import Helper
from Helper import *
from arcpy import env

def makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld, maxCost = None, attFld = None):

   import numpy
   arcpy.env.snapRaster = costRastLoc
   arcpy.env.cellSize = costRastLoc
   arcpy.env.extent = costRastLoc
   arcpy.env.overwriteOutput = True
   arcpy.env.outputCoordinateSystem = costRastLoc
   arcpy.env.parallelProcessingFactor = "50%" # "0" # don't use parallel processing. For very large rasters PP could help, but for smaller it's a slowdown

   make_gdb(outGDB)
   arcpy.env.workspace = outGDB
   arcpy.SetLogHistory(False)

   # copy access points to gdb
   accFeat = arcpy.CopyFeatures_management(accFeat, 'accFeat_orig')
   grps = unique_values(accFeat, grpFld)
   gdbct = 1

   if isinstance(attFld, str):
      arcpy.AddField_management(accFeat, 'minutes_SA', 'FLOAT')
      # adjust codeblock here for custom SA minutes values
      codeblock = """def fn(score, maxcost):
         # min = round(15 * (math.log10(score + 5)), 1) # public lands
         min = round(30 * (math.log10(score + 1.5)), 1) # trails
         if min > maxcost:
            return maxcost
         else:
            return min"""
      arcpy.CalculateField_management(accFeat, 'minutes_SA', 'fn(!' + attFld + '!,' + str(maxCost) + ')', 'PYTHON', codeblock)
   else:
      arcpy.AddField_management(accFeat, 'minutes_SA', 'FLOAT')
      arcpy.CalculateField_management(accFeat, 'minutes_SA', maxCost, 'PYTHON')

   for i in grps:
      n = grps.index(i) + 1
      if isinstance(i, str):
         rastout = "grp_" + i + "_servArea"
         cdpts = "grp_" + i + "_inputPts"
         i_q = "'" + i + "'"
      else:
         rastout = "grp_" + str(int(i)) + "_servArea"
         cdpts = "grp_" + str(int(i)) + "_inputPts"
         i_q = i
      if arcpy.Exists(rastout):
         # skip already existing
         continue
      #if n / 500 > gdbct:
      #   newGDB = re.sub('[0-9]*.gdb$', '', outGDB) + str(int(gdbct)) + ".gdb"
      #   make_gdb(newGDB)
      #   arcpy.env.workspace = newGDB
      #   gdbct = gdbct + 1

      print("working on group " + str(i) + " (" + str(n) + " of " + str(len(grps)) + ")...")
      arcpy.env.extent = costRastLoc  # so points don't get excluded due to previous extent setting
      t0 = time.time()
      c = 1  # counter

      tmpGrp = arcpy.MakeFeatureLayer_management(accFeat, cdpts, grpFld + " = " + str(i_q))
      arcpy.CopyFeatures_management(tmpGrp, cdpts)

      # get service area in minutes
      maxCost = round(unique_values(cdpts, 'minutes_SA')[0], 1)
      buffd = str(int(maxCost * 1900)) + ' METERS'

      print('Cost in minutes: ' + str(maxCost))

      arcpy.Buffer_analysis(cdpts, "buffpts", buffd)
      arcpy.env.extent = "buffpts"

      print("# of access pts: " + arcpy.GetCount_management(cdpts)[0])

      # local CD
      cd1 = arcpy.sa.CostDistance(cdpts, costRastLoc, maxCost)
      nm = "cd" + str(c)
      cd1.save(nm)
      # values to ramps
      rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd1, "rp1", "NONE", "VALUE_ONLY")
      rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")

      if int(arcpy.GetCount_management(rp1s)[0]) == 0:
         if attFld:
            if isinstance(attFld, str):
               areaval = unique_values(cdpts, attFld)[0]
               area = arcpy.sa.Con("cd1", areaval, "", "Value <= " + str(maxCost))
               area.save(rastout)
            elif isinstance(attFld, int):
               area = arcpy.sa.Con("cd1", attFld, "", "Value <= " + str(maxCost))
               area.save(rastout)
         else:
            cd1.save(rastout)
      else:
         # run highways CD if cd1 reaches any ramps
         notin = [1]
         allr = []
         while len(notin) != 0:
            print('Limited-access cost distance run # ' + str(int((c+1)/2)) + '...')
            arcpy.CopyFeatures_management(rp1s, "rp1s")
            # highway CD
            cd2 = arcpy.sa.CostDistance("rp1s", costRastHwy, maxCost, source_start_cost="RASTERVALU")

            c = c + 1
            nm = "cd" + str(c)
            cd2.save(nm)

            rp2 = arcpy.sa.ExtractValuesToPoints(rampPts, cd2, "rp2", "NONE", "VALUE_ONLY")
            rp2s = arcpy.MakeFeatureLayer_management(rp2, where_clause="RASTERVALU IS NOT NULL")
            arcpy.AddField_management(rp2s, "costLAH", "DOUBLE")
            arcpy.CalculateField_management(rp2s, "costLAH", "!RASTERVALU!")

            # back to local
            if int(arcpy.GetCount_management(rp2s)[0]) != 0:
               used_ramps2 = unique_values(rp2s, rampPtsID)
               arcpy.CopyFeatures_management(rp2s, "rp2s")
               # FIXME: This is where it tends to "hang" with Pro 2.3+...
               cd3 = arcpy.sa.CostDistance("rp2s", costRastLoc, maxCost, source_start_cost="RASTERVALU")
            else:
               cd3 = cd2

            # write raster
            c = c + 1
            nm = "cd" + str(c)
            cd3.save(nm)

            allr = list(set(allr + used_ramps2))

            rp1 = arcpy.sa.ExtractValuesToPoints(rampPts, cd3, "rp1", "NONE", "VALUE_ONLY")
            rp1s = arcpy.MakeFeatureLayer_management(rp1, where_clause="RASTERVALU IS NOT NULL")
            arcpy.AddField_management(rp1s, "costLoc", "DOUBLE")
            arcpy.CalculateField_management(rp1s, "costLoc", "!RASTERVALU!")

            # join two ramp datasets together
            arcpy.JoinField_management(rp1s, rampPtsID, rp2s, rampPtsID, ["costLAH"])
            arcpy.AddField_management(rp1s, "diff", "DOUBLE")
            arcpy.CalculateField_management(rp1s, "diff", '!costLoc! - !costLAH!', 'PYTHON')
            # if this value is < 0, then this resulted in a faster route to that point. Should be kept for next run.
            sc1s = arcpy.da.SearchCursor(rp1s, [rampPtsID, "diff"])
            notin = []
            # add new ramps or ramps with at least 1m faster route after local run. This leads to another LAH/local loop
            for r in sc1s:
               if r[1] is None or r[1] < -1:
                  notin.append(r[0])

         # list of cd rasters
         cds = list(range(1, c + 1))
         newls = ['cd' + str(s) for s in cds]
         newls = ';'.join(newls)

         if attFld:
            if isinstance(attFld, str):
               # cell statistics
               areaval = unique_values(cdpts, attFld)[0]
               area = arcpy.sa.Con(arcpy.sa.CellStatistics(newls, "MINIMUM", "DATA"), areaval, "", "Value <= " + str(maxCost))
               area.save(rastout)
            elif isinstance(attFld, int):
               area = arcpy.sa.Con(arcpy.sa.CellStatistics(newls, "MINIMUM", "DATA"), attFld, "", "Value <= " + str(maxCost))
               area.save(rastout)
         else:
            arcpy.sa.CellStatistics(newls, "MINIMUM", "DATA").save(rastout)

      print("Done with group: " + str(i))
      t1 = time.time()
      print('That took ' + str(int(t1 - t0)) + ' seconds.')

      # garbage pickup every 10 runs
      if n == round(n, -1):
         print("Deleting files...")
         garbagePickup([rp1, rp1s, rp2, rp2s, cd1, cd2, cd3])
         r = arcpy.ListRasters("cd*")
         fc = arcpy.ListFeatureClasses("rp*")
         fc.append("buffpts")
         garbagePickup(r)
         garbagePickup(fc)

   # final clean up
   print("Deleting files...")
   garbagePickup([rp1, rp1s, rp2, rp2s, cd1, cd2, cd3])
   r = arcpy.ListRasters("cd*")
   fc = arcpy.ListFeatureClasses("rp*")
   fc.append("buffpts")
   garbagePickup(r)
   garbagePickup(fc)
   return

## end

def main():

   #################################
   # Set up variables
   costRastLoc = r'E:\RCL_cost_surfaces\Tiger_2018\cost_surfaces.gdb\costSurf_no_lah'
   costRastHwy = r'E:\RCL_cost_surfaces\Tiger_2018\cost_surfaces.gdb\costSurf_only_lah'
   rampPts = r'E:\RCL_cost_surfaces\Tiger_2018\cost_surfaces.gdb\rmpt_final'
   rampPtsID = 'UniqueID'  # unique ramp segment ID attribute field, since some ramps have multiple points

   facil_date = 't_ttrl_20190225' # suffix of filename
   outGDB = r'E:\arcpro_wd\rec_model_temp\serviceAreas_modelupdate_Feb2019\access_' + facil_date + '.gdb'
   accFeat = r'E:\arcpro_wd\rec_model_temp\access_pts.gdb\access_' + facil_date
   grpFld = 'join_fid'  # [group_id for aquatic; join_fid for terrestrial]; name of attribute field of group for access points
   maxCost = 60  # maximum cost distance in minutes; if attFld is set to a field name, variable costs will be calculated, but maxCost still applies
   attFld = 'join_score' # (optional) name of attribute field containing value to assign to raster for the group. or an integer value to apply
   makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld, maxCost, attFld)

   # time to closest facility (all points considered at once). Returns actual cost distance in minutes.
   accFeat = r'E:\arcpro_wd\rec_model_temp\access_pts.gdb\access_all_forregional'
   outGDB = r'E:\arcpro_wd\rec_model_temp\serviceAreas_modelupdate_Feb2019\access_all_60min.gdb'
   grpFld = 'facil_code'
   maxCost = 60  # in minutes
   attFld = None  # will return actual cost distance
   makeServiceAreas(outGDB, accFeat, costRastLoc, costRastHwy, rampPts, rampPtsID, grpFld, maxCost, attFld)

if __name__ == '__main__':
   main()
