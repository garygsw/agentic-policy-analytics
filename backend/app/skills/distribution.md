---
name: distribution
description: The spread of values, not just a total or an average: prices across towns, PSI across months, or any measure by group.
---
1. Decide what one value is: each period, and each value of any dimension you leave unfiltered, is one value. Filter dimensions that should not be pooled (for example flat_type).
2. `compute_distribution` with group_by the dimension to compare (or none, for one overall spread): it records each group's median and quartiles as facts, and names the dimensions it pooled.
3. `create_chart` box (many values) or dot (up to 600 values, so every value shows) with the same series and group_by.

Report the median and the range between the quartiles (where the middle half lies), and say what one value is. Caveats to state: a few values per group make quartiles unreliable; pooled dimensions mix different things.
