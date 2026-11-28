select
    league || '-' || event_id as match_id,
    team, athlete_id, player,
    shots, shots_on_target, goals, assists, own_goals
from {{ source('espn_raw', 'players') }}
