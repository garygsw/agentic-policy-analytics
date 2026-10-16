---
name: elections
description: General election results by constituency (1955 to 2025): vote shares, changes between elections, and a map on that election's boundaries. Neutral figures only.
---
Data (Extraction agent): load `ge_results`.

Analysis (Analytics agent):
1. Choose the measure: `vote_share` (per cent of valid votes, for one party: filter party) or `vote_count` (which adds up across parties and constituencies). Periods are election years only.
2. `compare_groups` of `vote_share` grouped by `constituency`, filtered to one party, for one election (period = its year).
3. `create_chart` map of the same, group_by `constituency`, period = the election year: it is drawn on that election's own boundaries (2006 to 2025). Uncontested constituencies (walkovers) have no votes and are grey.
4. To compare elections, use national totals (`vote_count` added up across constituencies) or the same constituency name only with care: boundaries change at every election.

Report figures neutrally: no judgement of parties or candidates, and no prediction. Caveats to state: walkovers are missing; a GRC elects a team; boundaries change between elections.
