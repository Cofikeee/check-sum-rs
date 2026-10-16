select name_normalized
from {{ ref('silver_merchants') }}
where name_normalized ~* '(^|[[:space:]])D[.]?[[:space:]]*O[.]?[[:space:]]*O[.]?([[:space:]]|$)'
   or name_normalized ~* '(^|[[:space:]])Д[.]?[[:space:]]*О[.]?[[:space:]]*О[.]?([[:space:]]|$)'
   or name_normalized ~* '(^|[[:space:]])(NOVI[[:space:]]+SAD|BEOGRAD|SERBIA|SRBIJA|RS|NS)([[:space:]]|$)'
