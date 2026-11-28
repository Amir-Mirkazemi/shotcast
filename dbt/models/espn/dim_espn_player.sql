-- One row per ESPN athlete. The current club is the team in his newest stored match (not a transfer record).
select
    p.athlete_id,
    mode(p.player)                    as player_name,
    arg_max(p.team, m.match_date)     as current_team,
    count(distinct p.match_id)        as matches,
    min(m.match_date)                 as first_match,
    max(m.match_date)                 as last_match
from {{ ref('stg_espn_players') }} p
join {{ ref('stg_espn_matches') }} m using (match_id)
where p.athlete_id is not null
group by p.athlete_id
