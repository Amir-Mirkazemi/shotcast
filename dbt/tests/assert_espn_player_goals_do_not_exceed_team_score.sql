-- Own goals are stored separately, so a team's players can never have scored more than the team's score.
with scored as (
    select match_id, team, sum(goals) as player_goals from {{ ref('fct_espn_player_match') }} group by 1, 2
),
score as (
    select match_id, home_team as team, home_score as goals from {{ ref('stg_espn_matches') }}
    union all
    select match_id, away_team, away_score from {{ ref('stg_espn_matches') }}
)
select s.* , c.goals as team_score
from scored s join score c using (match_id, team)
where c.goals is not null and s.player_goals > c.goals
