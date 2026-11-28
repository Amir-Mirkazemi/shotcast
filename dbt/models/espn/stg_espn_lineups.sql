select
    league || '-' || event_id as match_id,
    team, athlete_id, player, starter, minutes
from {{ source('espn_raw', 'lineups') }}
