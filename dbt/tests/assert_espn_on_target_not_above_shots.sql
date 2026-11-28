-- ESPN counts a goal as on target, so a goal with no listed shot is tolerated; anything beyond that is bad data.
select * from {{ ref('fct_espn_player_match') }}
where shots_on_target > greatest(shots, goals)
