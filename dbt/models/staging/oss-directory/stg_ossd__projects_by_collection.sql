{#
  Many to many relationship table for collections
#}

SELECT 
  c.id as `collection_id`,
  p.id as `project_id`
FROM {{ ref('stg_ossd__current_collections')}} AS c
CROSS JOIN UNNEST(c.projects) AS project_slug
JOIN {{ ref('stg_ossd__current_projects') }} AS p
  ON p.slug = project_slug