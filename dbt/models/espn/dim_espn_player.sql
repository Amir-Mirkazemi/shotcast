-- One row per ESPN athlete, including players who played but have no stat line (they are in the lineup only).
-- The current club is the team in his newest stored match (not a transfer record).
with appearances as (
    select match_id, athlete_id, player, team from {{ ref('stg_espn_players') }} where athlete_id is not null
    union
    select match_id, athlete_id, player, team from {{ ref('stg_espn_lineups') }} where athlete_id is not null
)
select
    a.athlete_id,
    mode(a.player)                    as player_name,
    arg_max(a.team, m.match_date)     as current_team,
    count(distinct a.match_id)        as matches,
    min(m.match_date)                 as first_match,
    max(m.match_date)                 as last_match
from appearances a
join {{ ref('stg_espn_matches') }} m using (match_id)
group by a.athlete_id
