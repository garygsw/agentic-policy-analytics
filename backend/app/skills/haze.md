---
name: haze
description: Haze in Singapore and the fires that cause it: PSI by region next to fire hotspots across Southeast Asia, with a map of the fires.
---
Data (Extraction agent): load `psi_by_region` and `fire_hotspots`. Search documents or the web only for policy context, such as the Transboundary Haze Pollution Act or ASEAN haze agreements, if the question asks.

Analysis (Analytics agent):
1. `compute_trend` on `psi_by_region`, measure `highest_psi` for one region (south was worst in 2015), or `unhealthy_days` to count days above 100. Regions cannot be added up.
2. `create_chart` heatmap of `psi_by_region` `highest_psi`, group_by `region`: the months run across, and haze episodes such as September 2015 and September 2019 stand out.
3. `compare_groups` on `fire_hotspots`, measure `hotspots`, group_by `country`, period = the worst season (for example 2015): which countries the fires were in.
4. `create_chart` map of `fire_hotspots` `hotspots`, group_by `cell` (where the fires were densest) or `country`, period = that season. Singapore is marked on the map.
5. To relate fires and haze: `compute_correlation` of `fire_hotspots` `hotspots` (filter country Indonesia, or all countries) with `psi_by_region` `highest_psi` (one region). The tool puts monthly PSI on calendar years (averages of the monthly peaks, complete years only); say so. Then `create_chart` scatter of the same two series.

Caveats to state: a hotspot is a satellite detection, not a fire, and cloud or smoke can hide fires; each hotspot year is its July-October season; PSI regions are NEA reporting areas; correlation shows co-movement, not cause.
