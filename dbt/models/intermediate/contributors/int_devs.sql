SELECT
  e.project_id,
  e.from_id,
  TIMESTAMP_TRUNC(e.time, MONTH) AS bucket_month,
  CASE 
    WHEN COUNT(DISTINCT CASE WHEN e.event_type = 'COMMIT_CODE' THEN e.time END) >= 10 THEN 'FULL_TIME_DEV'
    WHEN COUNT(DISTINCT CASE WHEN e.event_type = 'COMMIT_CODE' THEN e.time END) >= 1 THEN 'PART_TIME_DEV'
    ELSE 'OTHER_CONTRIBUTOR'
  END AS user_segment_type,
  1 AS amount
FROM {{ ref('int_events_to_project') }} as e
WHERE 
  e.event_type IN (
    'PULL_REQUEST_CREATED',
    'PULL_REQUEST_MERGED',
    'COMMIT_CODE',
    'ISSUE_CLOSED',
    'ISSUE_CREATED'
  )
GROUP BY 1,2,3