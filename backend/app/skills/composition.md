---
name: composition
description: Parts of a whole: how a total divides (employment by sector, votes by party), and how that mix compares across groups.
---
1. Check the profile: parts are only parts of a whole if the measure can be added up across that dimension (can_add_up_across). Prices, rates and medians cannot.
2. `compare_groups` grouped by the parts for one period: it records each part's value and its share of the total.
3. For one whole with a few parts (up to about seven): `create_chart` pie, group_by the parts.
4. To compare the mix across several groups: `create_chart` stacked_bar, group_by the groups and column_by the parts. If the measure does not add up across the parts, use grouped_bar instead, which compares the parts side by side.

Caveats to state: shares hide totals (a smaller share of a bigger total can be more); say which period.
