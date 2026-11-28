select * exclude (event_id) from {{ ref('stg_espn_matches') }}
