select id, product_name_source, product_name_normalized
from {{ ref('silver_slipapp_items') }}
where product_name_source is null
   or product_name_normalized is null
   or product_name_normalized <> upper(product_name_normalized)
   or product_name_normalized ~ '>>[^<]+<<'

union all

select id, product_name_source, product_name_normalized
from {{ ref('silver_input_entries') }}
where product_name_source is null
   or product_name_normalized is null
   or product_name_normalized <> upper(product_name_normalized)
   or product_name_normalized ~ '>>[^<]+<<'
