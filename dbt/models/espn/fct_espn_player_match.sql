-- Grain: one row per player per match. Stats come from ESPN's player stats, minutes from the lineup.
-- A player who played but has no stat line gets zeros; minutes stay null where the lineup is missing.
with joined as (
    select
        coalesce(s.match_id, l.match_id)           as match_id,
        coalesce(s.athlete_id, l.athlete_id)       as athlete_id,
        coalesce(s.team, l.team)                   as team,
        coalesce(s.shots, 0)                       as shots,
        coalesce(s.shots_on_target, 0)             as shots_on_target,
        coalesce(s.goals, 0)                       as goals,
        coalesce(s.assists, 0)                     as assists,
        l.minutes,
        l.starter
    from {{ ref('stg_espn_players') }} s
    full outer join {{ ref('stg_espn_lineups') }} l
        on s.match_id = l.match_id and s.athlete_id = l.athlete_id
    where coalesce(s.athlete_id, l.athlete_id) is not null
)
select
    j.match_id || '-' || j.athlete_id as player_match_id,
    j.*,
    m.league, m.season, m.match_date
from joined j
join {{ ref('stg_espn_matches') }} m using (match_id)
