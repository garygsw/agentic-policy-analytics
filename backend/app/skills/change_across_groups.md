---
name: change_across_groups
description: Which groups rose or fell most over a period (towns, industries, regions), and the pattern over time.
---
1. `rank_changes` once, grouped by the dimension, between the two periods the question names (or the first and latest): it compares every group over the same periods. Do not call compute_trend group by group.
2. `create_chart` heatmap of the measure, group_by the dimension: groups down the side, periods across, to show when each one moved.
3. For the two to four groups that matter most: `compute_trend` for each, then one `create_chart` line chart with those series.

Caveats to state: percentage changes from small bases look large; say which periods were compared.
