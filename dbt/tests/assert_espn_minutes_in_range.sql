select * from {{ ref('fct_espn_player_match') }}
where minutes < 0 or minutes > 150
