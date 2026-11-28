select s.*, m.season, m.match_date
from {{ ref('stg_espn_shots') }} s
join {{ ref('stg_espn_matches') }} m using (match_id)
