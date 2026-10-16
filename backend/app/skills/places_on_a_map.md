---
name: places_on_a_map
description: Compare a measure across places (HDB towns, planning areas, URA or NEA regions, electoral divisions, Southeast Asian countries) with a map and a ranking.
---
1. Find the place dimension: `describe_dataset` on it (for example `town`, `region`, `constituency`, `country`) and filter the other dimensions to one value each (for example flat_type 4-room).
2. `compare_groups` grouped by the place dimension for one period (the latest unless the question names one), or `rank_changes` between two periods if the question is about change.
3. `create_chart` map, group_by the place dimension, period as above: areas are shaded light (low) to dark (high). The chart's note says which places could not be drawn; mention them. The Central Area town is drawn as URA's Central Area planning areas.
4. `create_chart` bar of the same values, so exact figures can be read and compared.

Caveats to state: a map shows one period; small places can hide large values; places with no data are grey.
