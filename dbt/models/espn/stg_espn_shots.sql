select
    league || '-' || event_id || '-' || play_id as shot_id,
    league || '-' || event_id                   as match_id,
    league, team, player, assist_player, outcome, on_target,
    period, seconds, x, y,
    (x is not null and y is not null and not (x = 0 and y = 0)) as has_location,
    body_part, situation
from {{ source('espn_raw', 'shots') }}
