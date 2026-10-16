select 'merchants' as relation_name, id
from {{ ref('silver_merchants') }}
where id !~ '^[0-9a-f]{64}$'

union all

select 'slipapp_receipts', id
from {{ ref('silver_slipapp_receipts') }}
where id !~ '^[0-9a-f]{64}$'

union all

select 'products', id
from {{ ref('silver_products') }}
where id !~ '^[0-9a-f]{64}$'

union all

select 'slipapp_items', id
from {{ ref('silver_slipapp_items') }}
where id !~ '^[0-9a-f]{64}$'

union all

select 'input_entries', id
from {{ ref('silver_input_entries') }}
where id !~ '^[0-9a-f]{64}$'
