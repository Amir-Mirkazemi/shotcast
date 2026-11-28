with sides as (
    select home_team as team_name, match_id, match_date, league from {{ ref('stg_espn_matches') }}
    union all
    select away_team, match_id, match_date, league from {{ ref('stg_espn_matches') }}
)
select
    team_name,
    count(*)                       as matches,
    min(match_date)                as first_match,
    max(match_date)                as last_match,
    count(distinct league)         as competitions
from sides
group by team_name
