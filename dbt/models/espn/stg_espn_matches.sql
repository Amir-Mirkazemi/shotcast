select
    league || '-' || event_id                                   as match_id,
    league,
    event_id,
    match_date,
    {{ season_label('match_date') }}                            as season,
    home_team,
    away_team,
    home_score,
    away_score,
    home_score + away_score                                     as total_goals,
    case when home_score > away_score then 'home'
         when home_score < away_score then 'away'
         when home_score = away_score then 'draw' end           as result,
    coverage,
    shot_count,
    loaded_at
from {{ source('espn_raw', 'matches') }}
