-- The career table: one row per player, season, competition and club.
select
    athlete_id, season, league, team,
    count(*)                                         as matches,
    count(minutes)                                   as matches_with_minutes,
    coalesce(sum(minutes), 0)                        as minutes,
    sum(shots)                                       as shots,
    sum(shots_on_target)                             as shots_on_target,
    sum(goals)                                       as goals,
    sum(assists)                                     as assists,
    case when sum(minutes) >= 900
         then round(sum(shots_on_target) * 90.0 / sum(minutes), 2) end as sot_per_90
from {{ ref('fct_espn_player_match') }}
group by athlete_id, season, league, team
